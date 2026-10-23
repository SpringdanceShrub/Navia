from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np


LOCAL=timezone(timedelta(hours=-6),"America/El_Salvador")


def integrate_distance(length_m, departure, speed_at_hour, horizon_hours=168):
    if not math.isfinite(length_m) or length_m<0:
        raise ValueError("length_m debe ser finito y no negativo")
    current=departure.replace(tzinfo=LOCAL) if departure.tzinfo is None else departure.astimezone(LOCAL)
    start=current
    remaining=float(length_m)
    deadline=current+timedelta(hours=horizon_hours)
    while remaining>1e-7 and current<deadline:
        end=current.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
        seconds=min((end-current).total_seconds(),(deadline-current).total_seconds())
        speed=float(speed_at_hour(current))
        if not math.isfinite(speed) or speed<0:
            raise ValueError("Velocidad no valida")
        rate=speed/3.6
        if rate>0 and remaining<=rate*seconds:
            return (current-start).total_seconds()+remaining/rate
        remaining-=rate*seconds
        current+=timedelta(seconds=seconds)
    return (current-start).total_seconds() if remaining<=1e-7 else math.inf


def traverse_pieces(piece_ids, piece_lengths, departure, speed_at_time, reverse=False,
                    start_offset_m=0., end_offset_m=None, horizon_hours=168):
    """Traverse an ordered interval of a source road using actual arrival times.

    Offsets are always measured in the source's forward order; ``reverse`` reverses
    traversal within that interval. Source order is not a legal routing graph or
    proof that reverse travel is allowed. The caller supplies graph legality.
    """
    ids = np.asarray(piece_ids)
    lengths = np.asarray(piece_lengths, dtype=float)
    if ids.ndim != 1 or lengths.shape != ids.shape or not np.isfinite(lengths).all() or (lengths <= 0).any():
        raise ValueError('Subtramos y longitudes invalidos')
    if not math.isfinite(horizon_hours) or horizon_hours <= 0:
        raise ValueError('horizon_hours debe ser positivo y finito')
    cumulative = np.r_[0., np.cumsum(lengths)]
    start_offset_m = float(start_offset_m)
    end_offset_m = float(cumulative[-1]) if end_offset_m is None else float(end_offset_m)
    if (not math.isfinite(start_offset_m) or not math.isfinite(end_offset_m)
            or start_offset_m < 0 or end_offset_m < start_offset_m or end_offset_m > cumulative[-1] + 1e-6):
        raise ValueError('Intervalo del padre fuera de sus limites')
    selected_length = np.maximum(0., np.minimum(cumulative[1:], end_offset_m)
                                 - np.maximum(cumulative[:-1], start_offset_m))
    rows = np.flatnonzero(selected_length > 1e-7)
    if reverse:
        rows = rows[::-1]
    start = departure.replace(tzinfo=LOCAL) if departure.tzinfo is None else departure.astimezone(LOCAL)
    current = start
    traversed = []
    for row in rows:
        remaining_horizon = horizon_hours - (current - start).total_seconds() / 3600
        seconds = (integrate_distance(float(selected_length[row]), current,
                    lambda t, sid=int(ids[row]): speed_at_time(sid, t), remaining_horizon)
                   if remaining_horizon > 0 else math.inf)
        if not math.isfinite(seconds):
            return {'travel_time_s': math.inf, 'arrival_at_end_local': None,
                    'departure_local': start.isoformat(), 'length_m': end_offset_m - start_offset_m,
                    'children_traversed': traversed, 'reverse': bool(reverse)}
        traversed.append(int(ids[row]))
        current += timedelta(seconds=seconds)
    return {'travel_time_s': (current - start).total_seconds(),
            'arrival_at_end_local': current.isoformat(), 'departure_local': start.isoformat(),
            'length_m': end_offset_m - start_offset_m, 'children_traversed': traversed,
            'reverse': bool(reverse)}


