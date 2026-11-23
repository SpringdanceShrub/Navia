from pathlib import Path, PurePosixPath
import json

import numpy as np

from .calendar import holiday_context
from .costs import LOCAL
from .features import FEATURE_NAMES, HOLIDAYS, NetworkContext, activity_load, feature_matrix, validate_conditions
from .hashing import sha256


QUARTER_SCHEMA = "verdict41-quarter-model-v1"
WEEKLY_DTYPES = {
    "travel_time_s": np.dtype("<f4"),
    "typical_factor": np.dtype("<f4"),
    "slow_q": np.dtype("<u2"),
    "jam_q": np.dtype("<u2"),
}
STATIC_ARTIFACTS = {"road_times.npz", "road_context.npz", "conditional_residual.npz"}
CONDITION_DEFAULTS = {
    "rain_mm_h": 0., "holiday_scope": 1., "school_break": False,
    "incident_pressure": 0., "works_pressure": 0., "closed": False,
}


def _relative_path(name):
    if not isinstance(name, str) or "\\" in name or ":" in name:
        raise ValueError("Ruta de modelo inválida.")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != name:
        raise ValueError("Ruta de modelo inválida.")
    return path


def quarter_artifacts(manifest):
    """Validate the native contract and return files needed by the registry."""
    segments = manifest.get("segments")
    if (manifest.get("schema") != QUARTER_SCHEMA or manifest.get("minutes_per_slot") != 15
            or manifest.get("slots_per_day") != 96 or type(segments) is not int or segments < 1
            or manifest.get("weekly_shape") != [7, 96, segments]
            or manifest.get("time_zone") != "America/El_Salvador"
            or manifest.get("weekday_order") != "Monday=0 through Sunday=6"
            or manifest.get("time_contract") != "effective_link_time_in_seconds_with_control_wait_once"
            or manifest.get("condition_contract") != "apply_log_residual_to_moving_time_factor_only_then_add_control_wait"):
        raise ValueError("Contrato temporal de Verdict incompatible.")
    weekly = manifest.get("weekly_fields")
    if not isinstance(weekly, dict) or weekly.keys() != WEEKLY_DTYPES.keys():
        raise ValueError("Campos semanales de Verdict incompatibles.")
    paths = {_relative_path(name).as_posix() for name in weekly.values()}
    if len(paths) != len(weekly) or paths & STATIC_ARTIFACTS:
        raise ValueError("Los archivos semanales de Verdict deben ser distintos.")
    required = paths | STATIC_ARTIFACTS
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict) or not required <= hashes.keys():
        raise ValueError("Faltan archivos verificados del modelo.")
    for name, checksum in hashes.items():
        _relative_path(name)
        if (not isinstance(checksum, str) or len(checksum) != 64
                or any(character not in "0123456789abcdef" for character in checksum)):
            raise ValueError("Huella digital de modelo inválida.")
    return required


def _local(at):
    return at.replace(tzinfo=LOCAL) if at.tzinfo is None else at.astimezone(LOCAL)


class QuarterHourTrafficModel:

    minutes_per_slot = 15
    seconds_per_slot = 900

    def __init__(self, folder, manifest=None):
        self.folder = Path(folder).resolve(strict=True)
        if (self.folder / "INCOMPLETE").exists():
            raise ValueError("Modelo incompleto.")
        if manifest is None:
            manifest = json.loads((self.folder / "model_manifest.json").read_text(encoding="utf-8"))
        quarter_artifacts(manifest)
        self.name = manifest.get("name", self.folder.name)
        paths = {}
        for name, expected in manifest["artifact_sha256"].items():
            path = self.folder.joinpath(*_relative_path(name).parts).resolve(strict=True)
            if not path.is_relative_to(self.folder) or not path.is_file() or sha256(path) != expected:
                raise ValueError(f"El archivo del modelo no coincide: {name}")
            paths[name] = path

        self.thresholds = np.asarray(manifest.get("typical_class_thresholds"), dtype=np.float32)
        self.risk_scale = manifest.get("risk_scale")
        if (self.thresholds.shape != (2,) or not np.isfinite(self.thresholds).all()
                or not 1 <= self.thresholds[0] < self.thresholds[1]
                or type(self.risk_scale) is not int or not 1 <= self.risk_scale <= 65535
                or manifest.get("class_order") != ["NORMAL", "SLOW", "TRAFFIC_JAM"]):
            raise ValueError("Clasificación de tráfico de Verdict inválida.")

        n = manifest["segments"]
        with np.load(paths["road_times.npz"], allow_pickle=False) as roads:
            self.ids = roads["segment_ids"]
            self.length = np.asarray(roads["length_m"], dtype=np.float32)
            self.moving_time = np.asarray(roads["moving_time_s"], dtype=np.float32)
            self.control_wait = np.asarray(roads["control_wait_s"], dtype=np.float32)
        if (self.ids.dtype.kind != "i" or self.ids.shape != (n,) or np.any(self.ids[1:] <= self.ids[:-1])
                or any(array.shape != (n,) or not np.isfinite(array).all()
                       for array in (self.length, self.moving_time, self.control_wait))
                or np.any(self.length <= 0) or np.any(self.moving_time <= 0) or np.any(self.control_wait < 0)):
            raise ValueError("Tramos o tiempos de Verdict inválidos.")
        self.free_time = self.moving_time + self.control_wait
        self.free_speed = self.length * 3.6 / self.free_time
        if not np.isfinite(self.free_time).all() or not np.isfinite(self.free_speed).all() or np.any(self.free_speed <= 0):
            raise ValueError("Velocidades base de Verdict inválidas.")
        self.context = NetworkContext.load(paths["road_context.npz"])
        if (not np.array_equal(self.ids, self.context.ids)
                or self.context.arrays["length"].shape != (n,)
                or not np.allclose(self.length, self.context.arrays["length"], rtol=1e-5, atol=.05)):
            raise ValueError("El contexto de Verdict usa otra red vial.")
        with np.load(paths["conditional_residual.npz"], allow_pickle=False) as data:
            self.coefficients = np.asarray(data["coefficients"], dtype=np.float32)
            if data["format_version"].item() != 1 or tuple(data["feature_names"].tolist()) != FEATURE_NAMES:
                raise ValueError("Predictores condicionales de Verdict incompatibles.")
        if (self.coefficients.shape != (len(FEATURE_NAMES),) or not np.isfinite(self.coefficients).all()
                or np.any(self.coefficients < 0)):
            raise ValueError("Coeficientes condicionales de Verdict inválidos.")

        self.weekly = {}
        for field, name in manifest["weekly_fields"].items():
            path = paths[name]
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            if (not isinstance(array, np.memmap) or array.shape != (7, 96, n)
                    or array.dtype != WEEKLY_DTYPES[field] or not array.flags.c_contiguous
                    or array.offset + array.nbytes != path.stat().st_size):
                raise ValueError(f"Matriz semanal de Verdict inválida: {field}")
            self.weekly[field] = array

    def indices(self, segment_id):
        ids = np.asarray(segment_id)
        if ids.dtype.kind not in "iu":
            raise KeyError("Identificador de tramo inválido.")
        rows = np.searchsorted(self.ids, ids)
        if np.any(rows >= len(self.ids)) or np.any(self.ids[rows] != ids):
            raise KeyError("Tramo no incluido en el modelo.")
        return rows

    def _base_times(self, rows, day, slot):
        travel = np.asarray(self.weekly["travel_time_s"][day, slot, rows])
        if not np.isfinite(travel).all() or np.any((travel <= 0) | (travel < self.free_time[rows] - .0001)):
            raise ValueError("Tiempos semanales de Verdict inválidos.")
        return travel

    def _holiday_travel(self, travel, rows, slot, holiday):
        code = HOLIDAYS.index(holiday)
        coefficients = self.coefficients[(code - 1) * 4:code * 4]
        moving = self.moving_time[rows]
        wait = self.control_wait[rows]
        log_base = np.log(np.clip((travel - wait) / moving, 1., 8.))
        load = activity_load(code, slot / 4. - .375, self.context.subset(rows))
        delta = (-log_base * coefficients[0] - log_base / (1. + log_base) * coefficients[1]
                 + np.log1p(load) * coefficients[2] + np.log1p(4. * load) * coefficients[3])
        return moving * np.exp(np.clip(log_base + delta, 0., np.log(8.))) + wait

    def _conditional_travel(self, travel, rows, day, slot, holiday, supplied, shape):
        if holiday not in HOLIDAYS:
            raise ValueError("Festivo de Verdict desconocido.")
        if supplied.keys() - CONDITION_DEFAULTS.keys():
            raise ValueError("Condición de tráfico desconocida.")
        if holiday == "holiday_local" and supplied.get("holiday_scope") is None:
            raise ValueError("El festivo local necesita un ámbito espacial.")
        supplied = {**CONDITION_DEFAULTS, **supplied}
        if supplied["holiday_scope"] is None:
            supplied["holiday_scope"] = 1.
        values = {key: np.broadcast_to(value, shape).reshape(-1) for key, value in supplied.items()}
        values["holiday_code"] = np.full(len(rows), HOLIDAYS.index(holiday), dtype=np.int8)
        validate_conditions(values)
        active = ((values["rain_mm_h"] > 0) | ((holiday != "none") & (values["holiday_scope"] > 0))
                  | ((day < 5) & (values["school_break"] > 0))
                  | (values["incident_pressure"] > 0) | (values["works_pressure"] > 0))
        if active.any():
            travel = travel.copy()
            selected = rows[active]
            moving = self.moving_time[selected]
            wait = self.control_wait[selected]
            base = np.clip((travel[active] - wait) / moving, 1., 8.)
            features = feature_matrix(base, day, slot / 4. - .375, self.context.subset(selected),
                                      {key: value[active] for key, value in values.items()})
            factor = np.exp(np.clip(np.log(base) + features @ self.coefficients, 0., np.log(8.)))
            travel[active] = moving * factor + wait
        return travel, active, values["closed"].astype(bool)

    def _traffic_level(self, factor):
        return (factor >= self.thresholds[0]).astype(np.int8) + (factor >= self.thresholds[1]).astype(np.int8)

    def traffic_level_for_row(self, row, at, delay_factor):
        local = _local(at)
        if holiday_context(local.date())["holiday"] == "none":
            factor = self.weekly["typical_factor"][local.weekday(), local.hour * 4 + local.minute // 15, row]
            if not np.isfinite(factor) or factor < 1.:
                raise ValueError("Factor habitual de Verdict inválido.")
        else:
            factor = np.float32(delay_factor)
        return int(self._traffic_level(factor))

    def slot_speeds(self, at):
        local = _local(at)
        rows = slice(None)
        slot = local.hour * 4 + local.minute // 15
        travel = self._base_times(rows, local.weekday(), slot)
        holiday = holiday_context(local.date())["holiday"]
        if holiday != "none":
            travel = self._holiday_travel(travel, rows, slot, holiday)
        return np.divide(self.length, travel, dtype=np.float32)

    def predict_at(self, segment_id, at, conditions=None, use_calendar=True):
        local = _local(at)
        slot = local.hour * 4 + local.minute // 15
        day = local.weekday()
        indexes = self.indices(segment_id)
        shape = indexes.shape
        rows = indexes.reshape(-1)
        travel = self._base_times(rows, day, slot)
        typical = np.asarray(self.weekly["typical_factor"][day, slot, rows])
        if not np.isfinite(typical).all() or np.any(typical < 1.):
            raise ValueError("Factor habitual de Verdict inválido.")
        calendar = holiday_context(local.date())["holiday"] if use_calendar else "none"
        if conditions is None:
            active = calendar != "none"
            if active:
                travel = self._holiday_travel(travel, rows, slot, calendar)
            closed = np.zeros(len(rows), dtype=bool)
        else:
            supplied = dict(conditions(segment_id, local) if callable(conditions) else conditions)
            holiday = supplied.pop("holiday", calendar)
            travel, active, closed = self._conditional_travel(travel, rows, day, slot, holiday, supplied, shape)
        delay = travel / self.free_time[rows]
        level = self._traffic_level(np.where(active, delay, typical))

        slow = self.weekly["slow_q"][day, slot, rows]
        jam = self.weekly["jam_q"][day, slot, rows]
        if np.any(slow.astype(np.uint32) + jam > self.risk_scale):
            raise ValueError("Probabilidades semanales de Verdict inválidas.")
        values = {
            "delay_factor": np.where(closed, np.nan, delay),
            "travel_time_s": np.where(closed, np.nan, travel),
            "speed_kmh": np.where(closed, 0., self.length[rows] * 3.6 / travel),
            "traffic_level": np.where(closed, 3, level),
            "closed": closed,
            "typical_factor": typical,
            "probability_slow": slow.astype(np.float32) / self.risk_scale,
            "probability_jam": jam.astype(np.float32) / self.risk_scale,
        }
        return {key: value.reshape(shape) if shape else value.item() for key, value in values.items()}
