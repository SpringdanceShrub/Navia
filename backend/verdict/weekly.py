from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from .costs import LOCAL, integrate_distance, traverse_pieces


class SeasonalRoadRegressor:
    def __init__(self, model_path):
        with np.load(model_path,allow_pickle=False) as data:
            self.ids=data["segment_ids"]
            self.log_delay=data["mean_log_delay"]
            self.free_speed=data["free_flow_speed_kmh"]
            self.free_time=data["free_flow_time_s"]
            self.thresholds=data["thresholds"]
            self.parent_ids=(data['parent_segment_ids'] if 'parent_segment_ids' in data else self.ids.copy())
            self.subsegment_index=(data['subsegment_index'] if 'subsegment_index' in data else np.zeros(len(self.ids),np.int16))
            self.length=(data['length_m'] if 'length_m' in data else self.free_time*self.free_speed/3.6)
            self.format_version=int(data['format_version']) if 'format_version' in data else 2
        if not np.all(np.diff(self.ids)>0): raise ValueError("Model IDs must be sorted and unique")
        if (self.log_delay.shape!=(7,24,len(self.ids)) or self.parent_ids.shape!=self.ids.shape
                or self.subsegment_index.shape!=self.ids.shape or self.length.shape!=self.ids.shape
                or not np.isfinite(self.log_delay).all() or not np.isfinite(self.length).all()
                or np.any(self.length<=0) or np.any(self.free_time<=0) or np.any(self.free_speed<=0)):
            raise ValueError('Metadatos o parametros del modelo invalidos')
        order=np.lexsort((self.subsegment_index,self.parent_ids))
        parents,starts=np.unique(self.parent_ids[order],return_index=True)
        self.parent_index={int(parent):rows for parent,rows in zip(parents,np.split(order,starts[1:]))}
        for rows in self.parent_index.values():
            if len(np.unique(self.subsegment_index[rows]))!=len(rows):
                raise ValueError('Indices de hijos duplicados dentro de un padre')

    @staticmethod
    def _valid_time(weekday,hour):
        if (not np.isfinite(weekday).all() or not np.isfinite(hour).all()
                or np.any((weekday<0)|(weekday>6)|(weekday!=np.floor(weekday)))
                or np.any((hour<0)|(hour>23)|(hour!=np.floor(hour)))):
            raise ValueError('weekday debe ser 0..6 y hour 0..23, enteros')

    def predict(self, segment_id, weekday, hour):
        ids,w,h=np.broadcast_arrays(np.asarray(segment_id,dtype=np.int64),np.asarray(weekday),np.asarray(hour))
        self._valid_time(w,h)
        idx=np.searchsorted(self.ids,ids)
        if np.any(idx>=len(self.ids)) or np.any(self.ids[np.minimum(idx,len(self.ids)-1)]!=ids):
            raise KeyError("Tramo no incluido en la red entrenada")
        factor=np.exp(self.log_delay[w.astype(int),h.astype(int),idx])
        result={"delay_factor":factor,"travel_time_s":self.free_time[idx]*factor,
                "speed_kmh":self.free_speed[idx]/factor,
                "traffic_level":(factor>=self.thresholds[0]).astype(np.int8)+(factor>=self.thresholds[1]).astype(np.int8)}
        return {k:v.item() if np.ndim(v)==0 else v for k,v in result.items()}

    def predict_parent(self,parent_segment_id,weekday,hour):
        """Snapshot cost: sum every child's cost at the same weekday/hour.

        Use traverse_parent for an actual traversal crossing hours. A parent level
        describes its aggregate delay and can hide a short child-level queue.
        """
        parents,w,h=np.broadcast_arrays(np.asarray(parent_segment_id,dtype=np.int64),np.asarray(weekday),np.asarray(hour))
        self._valid_time(w,h)
        result={k:np.empty(parents.shape,dtype=np.float64) for k in
                ('delay_factor','travel_time_s','speed_kmh','length_m','free_flow_time_s',
                 'normal_length_fraction','slow_length_fraction','jam_length_fraction')}
        level=np.empty(parents.shape,dtype=np.int8)
        for j,(parent,day,hour_value) in enumerate(zip(parents.flat,w.flat,h.flat)):
            try: rows=self.parent_index[int(parent)]
            except KeyError: raise KeyError(f'Carretera padre no incluida: {parent}') from None
            factor=np.exp(self.log_delay[int(day),int(hour_value),rows])
            travel=float(np.sum(self.free_time[rows]*factor,dtype=np.float64))
            free=float(np.sum(self.free_time[rows],dtype=np.float64));length=float(np.sum(self.length[rows],dtype=np.float64))
            aggregate=travel/free
            child_level=(factor>=self.thresholds[0]).astype(np.int8)+(factor>=self.thresholds[1]).astype(np.int8)
            values={'delay_factor':aggregate,'travel_time_s':travel,'speed_kmh':length*3.6/travel,
                    'length_m':length,'free_flow_time_s':free}
            values.update({name:float(self.length[rows][child_level==state].sum()/length)
                           for state,name in enumerate(('normal_length_fraction','slow_length_fraction','jam_length_fraction'))})
            for key,value in values.items():result[key].flat[j]=value
            level.flat[j]=int(aggregate>=self.thresholds[0])+int(aggregate>=self.thresholds[1])
        result['traffic_level']=level
        return {k:v.item() if np.ndim(v)==0 else v for k,v in result.items()}

    def speed_kmh(self,segment_id,arrival_at_segment):
        t=(arrival_at_segment.replace(tzinfo=LOCAL) if arrival_at_segment.tzinfo is None
           else arrival_at_segment.astimezone(LOCAL))
        return self.predict(segment_id,t.weekday(),t.hour)['speed_kmh']

    def travel_time_s(self,segment_id,departure,length_m=None):
        row=int(np.searchsorted(self.ids,int(segment_id)))
        if row>=len(self.ids) or self.ids[row]!=int(segment_id):raise KeyError(f'Tramo no incluido: {segment_id}')
        return integrate_distance(float(self.length[row]) if length_m is None else float(length_m),
                                  departure,lambda t:self.speed_kmh(segment_id,t))

    def traverse_parent(self,parent_segment_id,departure,reverse=False,start_offset_m=0.,end_offset_m=None,horizon_hours=168):
        """FIFO traversal in source child order; graph legality is the caller's job."""
        try: rows=self.parent_index[int(parent_segment_id)]
        except KeyError: raise KeyError(f'Carretera padre no incluida: {parent_segment_id}') from None
        result=traverse_pieces(self.ids[rows],self.length[rows],departure,self.speed_kmh,
                              reverse,start_offset_m,end_offset_m,horizon_hours)
        result['parent_segment_id']=int(parent_segment_id)
        return result


