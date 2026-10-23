from pathlib import Path
from datetime import date,datetime
import json
import numpy as np
from .weekly import SeasonalRoadRegressor
from .features import NetworkContext,HOLIDAYS,FEATURE_NAMES,feature_matrix,validate_conditions
from .costs import LOCAL,integrate_distance,traverse_pieces
from .calendar import holiday_context

class ScenarioSchedule:
    def __init__(self,model,case):
        self.model=model;self.case=case;self.day=date.fromisoformat(case['date']);self._hour_cache={}
    def __call__(self,segment_id,at):
        from .scenarios import case_conditions
        if at.date()!=self.day:return {}
        hour=at.hour
        if hour not in self._hour_cache:
            self._hour_cache[hour]=case_conditions(self.model.context,self.case,hour)
        rows=self.model.indices(segment_id)
        values=self._hour_cache[hour]
        result={k:v[rows] for k,v in values.items() if k!='holiday_code'}
        result['holiday']=self.case.get('holiday','none')
        return result

class ConditionalTrafficModel(SeasonalRoadRegressor):
    def __init__(self,folder):
        from .hashing import sha256
        folder=Path(folder)
        if (folder/'INCOMPLETE').exists():raise ValueError('Conditional model incomplete')
        manifest=json.loads((folder/'model_manifest.json').read_text(encoding='utf8'))
        for name,digest in manifest['artifact_sha256'].items():
            if sha256(folder/name)!=digest:raise ValueError('Model artifact changed: '+name)
        self.name=manifest.get('name',folder.name)
        self.normal_model_path=folder/manifest.get('normal_model_path','base/traffic_model.npz')
        super().__init__(self.normal_model_path)
        self.context=NetworkContext.load(folder/'road_context.npz')
        with np.load(folder/'conditional_residual.npz',allow_pickle=False) as data:
            self.coefficients=data['coefficients'];names=tuple(data['feature_names'].tolist())
        if names!=FEATURE_NAMES or self.coefficients.shape!=(len(names),) or np.any(self.coefficients<0) or not np.isfinite(self.coefficients).all():
            raise ValueError('Conditional coefficients invalid')
        if not np.array_equal(self.ids,self.context.ids):raise ValueError('Model/context IDs differ')
        self.folder=folder

    def indices(self,segment_id):
        ids=np.asarray(segment_id,dtype=np.int64);index=np.searchsorted(self.ids,ids)
        if np.any(index>=len(self.ids)) or np.any(self.ids[np.minimum(index,len(self.ids)-1)]!=ids):raise KeyError('Unknown road ID')
        return index

    def predict(self,segment_id,weekday,hour,*,rain_mm_h=0.,holiday='none',holiday_scope=None,school_break=False,
                incident_pressure=0.,works_pressure=0.,closed=False):
        if holiday not in HOLIDAYS:raise ValueError('Unknown holiday kind')
        if holiday=='holiday_local' and holiday_scope is None:raise ValueError('Local holidays require an explicit spatial scope')
        if holiday_scope is None:holiday_scope=1.
        arrays=np.broadcast_arrays(np.asarray(segment_id,dtype=np.int64),np.asarray(weekday),np.asarray(hour),
            np.asarray(rain_mm_h),np.asarray(holiday_scope),np.asarray(school_break),np.asarray(incident_pressure),np.asarray(works_pressure),np.asarray(closed))
        shape=arrays[0].shape;ids,w,h,rain,scope,school,incident,works,shut=[v.ravel() for v in arrays]
        self._valid_time(w,h);index=self.indices(ids)
        inputs={'rain_mm_h':rain,'holiday_code':np.full(len(ids),HOLIDAYS.index(holiday),np.int8),
            'holiday_scope':scope,'school_break':school,'incident_pressure':incident,'works_pressure':works,'closed':shut}
        validate_conditions(inputs)
        base=np.exp(self.log_delay[w.astype(int),h.astype(int),index])
        active=(rain>0)|((HOLIDAYS.index(holiday)>0)&(scope>0))|(school>0)|(incident>0)|(works>0)
        factor=base.copy()
        if active.any():
            select=np.flatnonzero(active)
            x=feature_matrix(base[select],w[select],h[select],self.context.subset(index[select]),{k:v[select] for k,v in inputs.items()})
            factor[select]=np.clip(np.exp(np.log(base[select])+x@self.coefficients),1.012,8.)
        level=(factor>=self.thresholds[0]).astype(np.int8)+(factor>=self.thresholds[1]).astype(np.int8)
        speed=self.free_speed[index]/factor;travel=self.free_time[index]*factor;shut=shut.astype(bool)
        factor=factor.astype(float);travel=travel.astype(float);speed=speed.astype(float)
        factor[shut]=np.nan;travel[shut]=np.nan;speed[shut]=0.;level[shut]=3
        result={'delay_factor':factor,'travel_time_s':travel,'speed_kmh':speed,'traffic_level':level,'closed':shut}
        return {k:(v.reshape(shape).item() if not shape else v.reshape(shape)) for k,v in result.items()}

    def predict_at(self,segment_id,at,conditions=None,use_calendar=True):
        t=at.replace(tzinfo=LOCAL) if at.tzinfo is None else at.astimezone(LOCAL)
        supplied=conditions(segment_id,t) if callable(conditions) else dict(conditions or {})
        if use_calendar and 'holiday' not in supplied:supplied['holiday']=holiday_context(t.date())['holiday']
        return self.predict(segment_id,t.weekday(),t.hour,**supplied)

    def speed_kmh(self,segment_id,arrival_at_segment,conditions=None,use_calendar=True):
        return self.predict_at(segment_id,arrival_at_segment,conditions,use_calendar)['speed_kmh']

    def travel_time_s(self,segment_id,departure,length_m=None,conditions=None,use_calendar=True,horizon_hours=168):
        row=int(self.indices(segment_id))
        return integrate_distance(float(self.length[row]) if length_m is None else float(length_m),departure,
            lambda t:self.speed_kmh(segment_id,t,conditions,use_calendar),horizon_hours)

    def traverse_parent(self,parent_segment_id,departure,reverse=False,start_offset_m=0.,end_offset_m=None,
                        horizon_hours=168,conditions=None,use_calendar=True):
        try:rows=self.parent_index[int(parent_segment_id)]
        except KeyError:raise KeyError('Unknown parent road') from None
        result=traverse_pieces(self.ids[rows],self.length[rows],departure,
            lambda segment,at:self.speed_kmh(segment,at,conditions,use_calendar),
            reverse,start_offset_m,end_offset_m,horizon_hours)
        result['parent_segment_id']=int(parent_segment_id);return result

    def predict_parent(self,parent_segment_id,weekday,hour,**conditions):
        # Parent summaries must use conditional child predictions, including closures.
        parent=int(parent_segment_id)
        try:rows=self.parent_index[parent]
        except KeyError:raise KeyError('Unknown parent road') from None
        predicted=self.predict(self.ids[rows],weekday,hour,**conditions)
        length=float(self.length[rows].sum());free=float(self.free_time[rows].sum())
        blocked=bool(np.any(predicted['closed']));travel=float(np.sum(predicted['travel_time_s']))
        factor=travel/free
        return {'travel_time_s':travel,'delay_factor':factor,'speed_kmh':0. if blocked else length*3.6/travel,
            'traffic_level':3 if blocked else int(factor>=self.thresholds[0])+int(factor>=self.thresholds[1]),
            'closed':blocked,'length_m':length,'free_flow_time_s':free,
            **{key:float(self.length[rows][predicted['traffic_level']==state].sum()/length) for state,key in
                enumerate(('normal_length_fraction','slow_length_fraction','jam_length_fraction','closed_length_fraction'))}}

    def incident(self,day,segment_id,start_hour,end_hour,severity=.7,closed=False,radius_m=1800.,recovery_hours=2):
        self.indices(segment_id)
        if not isinstance(day,date) or isinstance(day,datetime):raise TypeError('day must be a local date')
        if any(not isinstance(v,int) for v in (start_hour,end_hour,recovery_hours)) or not 0<=start_hour<end_hour<=24 or not 0<=recovery_hours<=6:
            raise ValueError('Event hours must be integers within one day')
        if not np.isfinite(severity) or not 0<severity<=1:raise ValueError('severity must be in (0,1]')
        self.context.influence(int(segment_id),float(radius_m))
        return ScenarioSchedule(self,{'date':day.isoformat(),'kind':'closure' if closed else 'incident',
            'holiday':holiday_context(day)['holiday'],'school_break':False,'incident_target_id':int(segment_id),'start_hour':start_hour,
            'end_hour':end_hour,'recovery_hours':recovery_hours,'severity':severity,'queue_extent_m':float(radius_m)})
