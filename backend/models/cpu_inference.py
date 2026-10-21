"""NumPy-only feature/profile/inference contract. Never imports TensorFlow."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

VERSION = '2.0.0-early-own-simulator-support-calibration'
KINDS = ('weekday', 'saturday', 'sunday')
CATEGORICAL = ('highway', 'department', 'district', 'surface', 'control', 'traffic_direction')
NUMERIC = ('log_length', 'lanes', 'speed_limit_kmh', 'free_speed_kmh', 'capacity_pce_s',
           'x', 'y', 'heading_x', 'heading_y', 'is_link', 'lanes_observed', 'speed_limit_observed')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4*1024*1024), b''): h.update(block)
    return h.hexdigest()


def save_json(path, value):
    def convert(x):
        if isinstance(x, np.generic): return x.item()
        if isinstance(x, np.ndarray): return x.tolist()
        return str(x)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=convert, allow_nan=False), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def kind(weekday):
    return np.where(np.asarray(weekday) == 6, 2, np.where(np.asarray(weekday) == 5, 1, 0))


def time_features(hour, weekday):
    h, w = np.broadcast_arrays(np.asarray(hour, float), np.asarray(weekday, int))
    h, w = h.ravel(), w.ravel()
    cyclic = [f(h*2*np.pi*j/24) for j in range(1, 5) for f in (np.sin, np.cos)]
    cyclic += [np.sin(w*2*np.pi/7), np.cos(w*2*np.pi/7)]
    cyclic += [(w == i).astype(float) for i in range(7)]
    cyclic += [v*(w >= 5) for v in cyclic[:4]]
    return np.column_stack(cyclic).astype(np.float32)


def hour_keys(hour, weekday):
    """Linear interpolation at hourly centres; weekday changes across midnight."""
    h, w = np.broadcast_arrays(np.asarray(hour, float), np.asarray(weekday, int))
    left = np.floor(h-.5).astype(int); fraction = h-.5-left
    k0 = kind((w+np.floor_divide(left, 24)) % 7)*24 + left % 24
    right = left+1
    k1 = kind((w+np.floor_divide(right, 24)) % 7)*24 + right % 24
    return k0.astype(int), k1.astype(int), fraction.astype(np.float32)


def static_features(edges, train_mask, schema=None):
    d = edges.copy()
    d['log_length'] = np.log1p(d.length_m)
    dx = d.end_x-d.x; dy = d.end_y-d.y; norm = np.maximum(np.hypot(dx, dy), 1)
    d['heading_x'], d['heading_y'] = dx/norm, dy/norm
    numeric = d[list(NUMERIC)].to_numpy(np.float64)
    if not np.isfinite(numeric).all(): raise ValueError('Nonfinite static geometry/features')
    if schema is None:
        if not np.any(train_mask): raise ValueError('No train roads for feature fitting')
        mean = numeric[train_mask].mean(axis=0); scale = numeric[train_mask].std(axis=0)
        scale = np.where(scale < 1e-6, 1., scale)
        schema = dict(numeric=list(NUMERIC), categorical=list(CATEGORICAL), mean=mean.tolist(), scale=scale.tolist(),
                      categories={c: sorted(d.loc[train_mask, c].fillna('').astype(str).unique()) for c in CATEGORICAL})
    pieces = [(numeric-np.asarray(schema['mean']))/np.asarray(schema['scale'])]
    for c in CATEGORICAL:
        vocab = schema['categories'][c]
        ix = pd.Index(vocab).get_indexer(d[c].fillna('').astype(str))+1
        pieces.append(np.eye(len(vocab)+1, dtype=np.float32)[ix])
    # Index zero is an explicit unknown road; known unobserved roads keep their geometry.
    return np.vstack([np.zeros((1, sum(x.shape[1] for x in pieces))), np.column_stack(pieces)]).astype(np.float32), schema


class Profiles:
    """Train-only road/hour means, shrunk to department/type/control/hour priors.

    Counts are observations, not same-interval vehicle volume. Excluding an entire
    training date prevents target-encoding leakage into neural residual fitting.
    """
    def __init__(self, sums, counts, groups, group_sums=None, group_counts=None):
        self.sums, self.counts, self.groups = sums, counts, groups
        ng = int(np.max(groups))+1
        self.group_sums = np.zeros((ng, 72), np.float64) if group_sums is None else group_sums
        self.group_counts = np.zeros((ng, 72), np.float64) if group_counts is None else group_counts
        if group_sums is None:
            np.add.at(self.group_sums, groups, sums)
            np.add.at(self.group_counts, groups, counts)

    def at(self, road, key, exclude=None):
        road, key = np.broadcast_arrays(np.asarray(road, int), np.asarray(key, int))
        g = self.groups[road]
        total_s, total_c = self.sums[road, key], self.counts[road, key]
        gs, gc = self.group_sums[g, key], self.group_counts[g, key]
        # Fall back to the same local hour across day types, not fabricated speeds.
        h = key % 24
        hs = sum(self.group_sums[g, h+24*i] for i in range(3))
        hc = sum(self.group_counts[g, h+24*i] for i in range(3))
        if exclude is not None:
            total_s = total_s-exclude.sums[road, key]; total_c = total_c-exclude.counts[road, key]
            gs = gs-exclude.group_sums[g, key]; gc = gc-exclude.group_counts[g, key]
            hs = hs-sum(exclude.group_sums[g, h+24*i] for i in range(3))
            hc = hc-sum(exclude.group_counts[g, h+24*i] for i in range(3))
        global_s, global_c = self.group_sums.sum(0), self.group_counts.sum(0)
        if exclude is not None:
            global_s = global_s-exclude.group_sums.sum(0); global_c = global_c-exclude.group_counts.sum(0)
        alls = sum(global_s[h+24*i] for i in range(3)); allc = sum(global_c[h+24*i] for i in range(3))
        # 0.95 only if the entire training hour is unsupported, explicitly flagged.
        global_mean = np.divide(alls, allc, out=np.full(road.shape, .95), where=allc > 0)
        group_hour = (hs+5*global_mean)/(hc+5)
        group_specific = (gs+5*group_hour)/(gc+5)
        return ((total_s+3*group_specific)/(total_c+3)).astype(np.float32), total_c

    def predict(self, road, hour, weekday, exclude=None):
        k0, k1, a = hour_keys(hour, weekday)
        p0, c0 = self.at(road, k0, exclude); p1, c1 = self.at(road, k1, exclude)
        return (1-a)*p0+a*p1, (1-a)*c0+a*c1


def logit(x):
    x = np.clip(x, .0001, .9999)
    return np.log(x/(1-x)).astype(np.float32)


def sigmoid(x):
    return 1/(1+np.exp(-np.clip(x, -40, 40)))


def neural_time_features(hour, weekday, prior, support):
    return np.column_stack([time_features(hour, weekday), logit(prior)/5,
                            np.log1p(np.maximum(support, 0))/5]).astype(np.float32)


def numpy_network(weights, road, temporal, prior_logit):
    x = np.column_stack([weights['static'][road], weights['embedding'][road], temporal]).astype(np.float32)
    for i in range(3): x = np.maximum(x@weights[f'w{i}']+weights[f'b{i}'], 0)
    correction = .5*np.tanh((x@weights['w3']+weights['b3']).ravel())
    return correction


class VerdictV2:
    """Safe portable CPU predictor, no Keras/Python object deserialization.

    Call per directed edge at its estimated arrival time, NOT whole-trip departure.
    Support counts indicate synthetic training coverage, not calibrated certainty.
    """
    def __init__(self, folder, verify=True):
        self.folder = Path(folder); self.metadata = read_json(self.folder/'model_metadata.json')
        status = read_json(self.folder/'run_status.json')
        if status.get('status') != 'complete': raise ValueError('Model is not independently completed')
        if verify:
            for name, sha in read_json(self.folder/'artifact_hashes.json').items():
                if Path(name).name != name or digest(self.folder/name) != sha: raise ValueError('Bundle changed: '+name)
        self.edges = pd.read_parquet(self.folder/'edge_catalogue.parquet')
        self.index = pd.Index(self.edges.edge_id)
        with np.load(self.folder/'cpu_weights.npz', allow_pickle=False) as z: self.weights = dict(z)
        with np.load(self.folder/'profiles.npz', allow_pickle=False) as z:
            self.profiles = Profiles(z['sums'], z['counts'], z['groups'])
        self.beta = read_json(self.folder/'serving_selection.json')['residual_scale']
        self.train_counts = self.profiles.counts.sum(axis=1)

    def predict(self, edge_ids, when, batch_size=4096):
        if batch_size < 1: raise ValueError('Positive batch size required')
        ids = np.atleast_1d(edge_ids); road = self.index.get_indexer(ids)+1
        if np.any(road == 0): raise ValueError('Edge IDs not present in frozen directed catalogue')
        times = pd.DatetimeIndex([when]*len(ids) if np.ndim(when) == 0 else when)
        if len(times) != len(ids): raise ValueError('Timestamps and edge IDs must have equal lengths')
        if times.tz is None: times = times.tz_localize(self.metadata['timezone'])
        else: times = times.tz_convert(self.metadata['timezone'])
        hour = times.hour.to_numpy()+times.minute.to_numpy()/60+times.second.to_numpy()/3600
        weekday = times.dayofweek.to_numpy(); values = []; supports = []
        for at in range(0, len(ids), batch_size):
            r = road[at:at+batch_size]; h = hour[at:at+batch_size]; w = weekday[at:at+batch_size]
            prior, support = self.profiles.predict(r, h, w)
            correction = numpy_network(self.weights, r, neural_time_features(h, w, prior, support), logit(prior))
            values.append(sigmoid(logit(prior)+self.beta*correction)); supports.append(support)
        ratio = np.concatenate(values); support = np.concatenate(supports)
        e = self.edges.iloc[road-1]; free = e.discretized_free_speed_kmh.to_numpy()
        speed = ratio*free
        return pd.DataFrame(dict(edge_id=ids, predicted_speed_ratio=ratio, predicted_speed_kmh=speed,
            travel_time_estimate_s=e.length_m.to_numpy()/np.maximum(speed/3.6, 1/3.6),
            travel_time_uses_speed_floor=speed < 1,
            traffic_proxy=np.where(ratio < .5, 'TRAFFIC_JAM', np.where(ratio < .8, 'SLOW', 'NORMAL')),
            training_hour_support=support, unsupported_road=self.train_counts[road] == 0,
            sparse_hour_support=support < 3, synthetic=True, real_world_validated=False))
