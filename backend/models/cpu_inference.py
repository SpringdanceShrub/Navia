"""Frozen, portable NumPy inference contract. No TensorFlow, pickle or repository imports."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

VERSION = 'Verdict 2 Luna'
BINS = 96
KEYS = 3 * BINS
CATEGORICAL = ('highway', 'department', 'surface', 'control', 'traffic_direction')
NUMERIC = ('log_length', 'lanes', 'speed_limit_kmh', 'discretized_free_speed_kmh',
           'x', 'y', 'heading_x', 'heading_y', 'is_link', 'lanes_observed', 'speed_limit_observed')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, value):
    def convert(x):
        if isinstance(x, np.generic): return x.item()
        if isinstance(x, np.ndarray): return x.tolist()
        if isinstance(x, Path): return str(x)
        raise TypeError(type(x).__name__)
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=convert,
                                    allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def kind(weekday):
    return np.where(np.asarray(weekday) == 6, 2, np.where(np.asarray(weekday) == 5, 1, 0))


def time_keys(hour, weekday):
    """Interpolate quarter-hour CENTRES; carry local weekday across midnight."""
    h, w = np.broadcast_arrays(np.asarray(hour, float), np.asarray(weekday, int))
    left = np.floor(h * 4 - .5).astype(int)
    a = (h * 4 - .5 - left).astype(np.float32)
    right = left + 1
    k0 = kind((w + left // BINS) % 7) * BINS + left % BINS
    k1 = kind((w + right // BINS) % 7) * BINS + right % BINS
    return k0.astype(int), k1.astype(int), a


def time_features(hour, weekday):
    h, w = np.broadcast_arrays(np.asarray(hour, float), np.asarray(weekday, int))
    h, w = h.ravel(), w.ravel()
    cyc = [f(h * 2 * np.pi * j / 24) for j in range(1, 7) for f in (np.sin, np.cos)]
    values = cyc + [np.sin(w * 2 * np.pi / 7), np.cos(w * 2 * np.pi / 7)]
    values += [(w == i).astype(float) for i in range(7)]
    # Saturday and Sunday have independent interactions, not one weekend multiplier.
    values += [v * (w == day) for day in (5, 6) for v in cyc[:8]]
    return np.column_stack(values).astype(np.float32)


TIME_WIDTH = 41  # 37 calendar + prior + pooled support + specific support + independent dates


def logit(x):
    x = np.clip(x, .0001, .9999)
    return np.log(x / (1 - x)).astype(np.float32)


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -40, 40)))


def neural_time_features(hour, weekday, prior, support, specific_support=None, independent_dates=None):
    specific_support = support if specific_support is None else specific_support
    independent_dates = np.zeros_like(support) if independent_dates is None else independent_dates
    hour, weekday, prior, support, specific_support, independent_dates = [v.ravel() for v in np.broadcast_arrays(
        np.asarray(hour), np.asarray(weekday), np.asarray(prior), np.asarray(support),
        np.asarray(specific_support), np.asarray(independent_dates))]
    return np.column_stack([time_features(hour, weekday), logit(prior) / 5,
                            np.log1p(np.maximum(support, 0)) / 5,
                            np.log1p(np.maximum(specific_support, 0)) / 5,
                            np.minimum(np.maximum(independent_dates, 0), 10) / 10]).astype(np.float32)


def static_features(edges, train_mask, schema=None):
    d = edges.copy()
    d['log_length'] = np.log1p(d.length_m)
    dx, dy = d.end_x - d.x, d.end_y - d.y
    norm = np.maximum(np.hypot(dx, dy), 1)
    d['heading_x'], d['heading_y'] = dx / norm, dy / norm
    numeric = d[list(NUMERIC)].to_numpy(np.float64)
    if not np.isfinite(numeric).all(): raise ValueError('Nonfinite static features')
    if schema is None:
        if not np.any(train_mask): raise ValueError('No train-observed roads')
        mean, scale = numeric[train_mask].mean(0), numeric[train_mask].std(0)
        schema = dict(numeric=list(NUMERIC), categorical=list(CATEGORICAL), mean=mean.tolist(),
            scale=np.where(scale < 1e-6, 1., scale).tolist(), categories={
                c: sorted(d.loc[train_mask, c].fillna('').astype(str).unique()) for c in CATEGORICAL})
    pieces = [((numeric - schema['mean']) / schema['scale']).astype(np.float32)]
    for c in CATEGORICAL:
        vocab = schema['categories'][c]
        ix = pd.Index(vocab).get_indexer(d[c].fillna('').astype(str)) + 1
        pieces.append(np.eye(len(vocab) + 1, dtype=np.float32)[ix])
    # Projected coordinates describe position; do not use a giant district-ID one-hot.
    spatial = [f(d[c].to_numpy() * 2 * np.pi / metres) for c in ('x', 'y')
               for metres in (5000, 15000, 50000) for f in (np.sin, np.cos)]
    pieces.append(np.column_stack(spatial).astype(np.float32))
    joined = np.column_stack(pieces).astype(np.float32)
    return np.vstack([np.zeros((1, joined.shape[1]), np.float32), joined]), schema


class Profiles:
    """Observed-road-only quarter-hour profiles with regional structural shrinkage.

    Sums/counts and ALL fallback levels exclude the example's entire date during
    residual training. Missing exposure is never counted as a free-flow label.
    A specific day-type profile needs two independent dates; otherwise observed
    same-road clock history is pooled and the uncertainty scope stays explicit.
    """
    def __init__(self, sums, counts, road_map, groups, day_counts=None, policy='independent_day_pool'):
        self.sums, self.counts = sums, counts
        self.road_map, self.groups = np.asarray(road_map), np.asarray(groups)
        if policy not in ('independent_day_pool', 'observed_daytype'):
            raise ValueError('Unknown historical-profile policy')
        self.policy = policy
        # One observation per directed road/quarter/date. Real preparation
        # supplies exact calendar counts; inference never invents extra days.
        inferred = np.max(counts.reshape(len(counts), 3, BINS), axis=(0, 2))
        self.day_counts = np.asarray(inferred if day_counts is None else day_counts, np.float32)
        if self.day_counts.shape != (3,) or not np.isfinite(self.day_counts).all() or np.any(self.day_counts < 0):
            raise ValueError('Three nonnegative independent date counts required')
        ng = int(np.max(groups)) + 1
        self.gs = np.zeros((ng, KEYS), np.float64)
        self.gc = np.zeros_like(self.gs)
        observed = np.flatnonzero(self.road_map > 0)
        np.add.at(self.gs, self.groups[observed], sums[self.road_map[observed]])
        np.add.at(self.gc, self.groups[observed], counts[self.road_map[observed]])
        self.global_s, self.global_c = self.gs.sum(0), self.gc.sum(0)

    def at(self, road, key, exclude=None):
        road, key = np.broadcast_arrays(np.asarray(road, int), np.asarray(key, int))
        r, g = self.road_map[road], self.groups[road]
        s, c = self.sums[r, key], self.counts[r, key]
        gs, gc = self.gs[g, key], self.gc[g, key]
        h = key % BINS
        hs = sum(self.gs[g, h + BINS * i] for i in range(3))
        hc = sum(self.gc[g, h + BINS * i] for i in range(3))
        rs = sum(self.sums[r, h + BINS * i] for i in range(3))
        rc = sum(self.counts[r, h + BINS * i] for i in range(3))
        dates = self.day_counts.copy()
        global_s, global_c = self.global_s, self.global_c
        if exclude is not None:
            s, c = s - exclude.sums[r, key], c - exclude.counts[r, key]
            gs, gc = gs - exclude.gs[g, key], gc - exclude.gc[g, key]
            hs -= sum(exclude.gs[g, h + BINS * i] for i in range(3))
            hc -= sum(exclude.gc[g, h + BINS * i] for i in range(3))
            rs -= sum(exclude.sums[r, h + BINS * i] for i in range(3))
            rc -= sum(exclude.counts[r, h + BINS * i] for i in range(3))
            dates -= exclude.day_counts
            global_s, global_c = global_s - exclude.global_s, global_c - exclude.global_c
        c, gc, hc = np.maximum(c, 0), np.maximum(gc, 0), np.maximum(hc, 0)
        all_s = sum(global_s[h + BINS * i] for i in range(3))
        all_c = sum(global_c[h + BINS * i] for i in range(3))
        # Explicit unsupported structural prior; the predictor flags it, not evidence.
        fallback = np.divide(all_s, all_c, out=np.full(road.shape, .95), where=all_c > 0)
        group_hour = (hs + 5 * fallback) / (hc + 5)
        group_specific = (gs + 5 * group_hour) / (gc + 5)
        if self.policy == 'observed_daytype':
            # Continuous same-daytype shrinkage: never erase Saturday/Sunday
            # simply because only one independent date remains in a CV fold.
            return np.clip((s + group_specific) / (c + 1), 0, 1).astype(np.float32), c
        # With only one Saturday, BOTH the road mean and its regional Saturday
        # mean contain that same day's anomaly. More roads != independent days.
        # Use observed same-road clock history instead, and let the neural
        # calendar branch learn day-type effects without a rigid Saturday base.
        specific = (dates[key // BINS] >= 2) & (c >= 2)
        pooled = (rs + group_hour) / (np.maximum(rc, 0) + 1)
        precise = (s + group_specific) / (c + 1)
        value = np.where(specific, precise, pooled)
        support = np.where(specific, c, np.maximum(rc, 0))
        return np.clip(value, 0, 1).astype(np.float32), support

    def predict(self, road, hour, weekday, exclude=None):
        k0, k1, a = time_keys(hour, weekday)
        p0, c0 = self.at(road, k0, exclude)
        p1, c1 = self.at(road, k1, exclude)
        return (1 - a) * p0 + a * p1, (1 - a) * c0 + a * c1

    def evidence(self, road, hour, weekday, exclude=None):
        """Specific-daytype observations vs effective pooled history, never confidence."""
        k0, k1, a = time_keys(hour, weekday)
        r = self.road_map[np.asarray(road, int)]
        c0, c1 = self.counts[r, k0], self.counts[r, k1]
        dates = self.day_counts.copy()
        if exclude is not None:
            c0 = c0 - exclude.counts[r, k0]; c1 = c1 - exclude.counts[r, k1]
            dates -= exclude.day_counts
        return ((1-a)*np.maximum(c0, 0)+a*np.maximum(c1, 0),
                (1-a)*np.maximum(dates[k0//BINS], 0)+a*np.maximum(dates[k1//BINS], 0))


def blend_forecast(prior, neural, strength):
    """Validation-selected convex blend; no manual traffic/color multiplier."""
    if not 0 <= strength <= 1: raise ValueError('Forecast blend must be in [0,1]')
    return (1-strength)*np.asarray(prior) + strength*np.asarray(neural)


def numpy_network(weights, road, temporal):
    x = np.column_stack([weights['static'][road], weights['embedding'][road], temporal]).astype(np.float32)
    for i in range(3): x = np.maximum(x @ weights[f'w{i}'] + weights[f'b{i}'], 0)
    return sigmoid((x @ weights['w3'] + weights['b3']).ravel())


class VerdictLuna:
    """Verdict 2 Luna, standalone CPU inference.

    Example: model = VerdictLuna(allow_experimental=True)
    Then model.predict(directed_edge_ids, local_arrival_timestamps).

    This frozen candidate did not pass every synthetic acceptance check.
    Explicit opt-in does not approve it or change its predictions.
    No TensorFlow, GPU, web app or training repository is required.
    """
    name = 'Verdict 2 Luna'

    def __init__(self, folder=None, verify=True, *, allow_experimental=False):
        self.folder = Path(folder) if folder is not None else Path(__file__).resolve().parent
        self.metadata = read_json(self.folder / 'model_metadata.json')
        if self.metadata.get('model_name') != self.name:
            raise ValueError('Expected Verdict 2 Luna metadata')
        if (self.metadata.get('status') != 'evaluated_not_approved' or
                self.metadata.get('validation_and_test_passed') is not False):
            raise ValueError('Unexpected candidate status; do not manufacture an approval')
        if not allow_experimental:
            raise ValueError('Verdict 2 Luna is experimental; use allow_experimental=True explicitly')
        if verify:
            for name, sha in read_json(self.folder / 'artifact_hashes.json').items():
                if Path(name).name != name or digest(self.folder / name) != sha:
                    raise ValueError('Changed bundle artifact: ' + name)
        self.edges = pd.read_parquet(self.folder / 'edge_catalogue.parquet')
        self.index = pd.Index(self.edges.edge_id)
        with np.load(self.folder / 'cpu_weights.npz', allow_pickle=False) as z: self.weights = dict(z)
        with np.load(self.folder / 'profiles.npz', allow_pickle=False) as z:
            policy = str(z['policy'].item()) if 'policy' in z else 'independent_day_pool'
            self.profiles = Profiles(z['sums'], z['counts'], z['road_map'], z['groups'], z['day_counts'], policy)
        self.beta = self.metadata['neural_weight']
        if not 0 <= self.beta <= 1: raise ValueError('Invalid blend weight')
        self.train_counts = self.profiles.counts.sum(1)

    def predict(self, edge_ids, when, batch_size=4096):
        if batch_size < 1: raise ValueError('Positive batch size required')
        ids = np.atleast_1d(edge_ids)
        if not len(ids): return pd.DataFrame()
        road = self.index.get_indexer(ids) + 1
        if np.any(road == 0): raise ValueError('Unknown directed edge ID')
        times = pd.DatetimeIndex([when] * len(ids) if np.ndim(when) == 0 else when)
        if len(times) != len(ids) or times.isna().any(): raise ValueError('Invalid arrival timestamps')
        times = times.tz_localize(self.metadata['timezone']) if times.tz is None else times.tz_convert(self.metadata['timezone'])
        h = times.hour.to_numpy() + times.minute.to_numpy() / 60 + times.second.to_numpy() / 3600
        w = times.dayofweek.to_numpy()
        values, supports, specifics, dates_seen = [], [], [], []
        for at in range(0, len(ids), batch_size):
            r, hour, day = road[at:at + batch_size], h[at:at + batch_size], w[at:at + batch_size]
            prior, support = self.profiles.predict(r, hour, day)
            specific, dates = self.profiles.evidence(r, hour, day)
            neural = numpy_network(self.weights, r, neural_time_features(hour, day, prior, support, specific, dates))
            values.append(blend_forecast(prior, neural, self.beta)); supports.append(support)
            specifics.append(specific); dates_seen.append(np.broadcast_to(dates, r.shape))
        ratio, support = np.concatenate(values), np.concatenate(supports)
        e = self.edges.iloc[road - 1]
        speed = ratio * e.discretized_free_speed_kmh.to_numpy()
        return pd.DataFrame(dict(edge_id=ids, predicted_speed_ratio=ratio, predicted_speed_kmh=speed,
            travel_time_estimate_s=e.length_m.to_numpy() / (np.maximum(speed, 1) / 3.6),
            travel_time_uses_speed_floor=speed < 1,
            traffic_proxy=np.where(ratio < .5, 'TRAFFIC_JAM', np.where(ratio < .8, 'SLOW', 'NORMAL')),
            training_hour_support=support, unsupported_road=self.train_counts[self.profiles.road_map[road]] == 0,
            training_daytype_support=np.concatenate(specifics), independent_daytype_dates=np.concatenate(dates_seen),
            pooled_daytype_history=((np.concatenate(specifics) < 2) | (np.concatenate(dates_seen) < 2))
                if self.profiles.policy == 'independent_day_pool' else np.concatenate(specifics) == 0,
            sparse_daytype_history=(np.concatenate(specifics) < 2) | (np.concatenate(dates_seen) < 2),
            sparse_hour_support=support < 3, synthetic=True, real_world_validated=False))
