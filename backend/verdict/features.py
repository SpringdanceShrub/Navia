from pathlib import Path
import heapq
from functools import lru_cache
import numpy as np

HOLIDAYS=('none','holiday_quiet','holiday_outbound','holiday_beach','holiday_return',
    'holiday_shopping','holiday_civic','holiday_local')
FEATURE_NAMES=tuple(name for kind in HOLIDAYS[1:] for name in
    (kind+'_demand',kind+'_demand_saturation',kind+'_activity',kind+'_activity_saturation'))+(
    'school_break','school_break_urban','rain','rain_urban','rain_importance','rain_bridge','rain_squared',
    'incident_low','incident_medium','incident_high','works_low','works_medium','works_high','rain_incident')
INPUT_NAMES=('rain_mm_h','holiday_code','holiday_scope','school_break','incident_pressure','works_pressure','closed')

def activity_load(code,hour,static,xp=np):
    urban=static['urban'];tourism=static['tourism'];importance=static['importance'];activity=static['activity']
    g=lambda center,width:xp.exp(-.5*((hour+.5-center)/width)**2)
    if code in (2,3,4):
        return tourism*(.2+.8*importance)*g({2:10.,3:12.,4:17.}[code],2.2)
    if code==5:return urban*(.3+.7*importance)*(.3+.7*activity)*(g(12,2)+g(16.5,2))
    if code==6:return urban*(.3+.7*importance)*g(10.5,2)
    if code==7:return urban*(.3+.7*importance)*(.6*g(11,2)+g(18.5,2))
    return .15*tourism*g(13,3)

def feature_matrix(base,weekday,hour,static,conditions,xp=np):
    """Return N x features. Rows are flat; values are known at prediction time."""
    logbase=xp.log(base);code=conditions['holiday_code'];scope=conditions['holiday_scope']
    columns=[]
    zero=xp.zeros_like(base)
    for number in range(1,len(HOLIDAYS)):
        if not bool(xp.any(code==number)):
            columns.extend([zero,zero,zero,zero]);continue
        active=(code==number)*scope
        load=activity_load(number,hour,static,xp)*active
        columns.extend([-logbase*active,-logbase/(1+logbase)*active,xp.log1p(load),xp.log1p(4*load)])
    g=lambda center,width:xp.exp(-.5*((hour+.5-center)/width)**2)
    window=g(6.65,.7)+.8*g(12.15,.8)+.4*g(17.65,.8)
    school=conditions['school_break']*(weekday<5)*static['school']*window
    columns.extend([-logbase*school,-logbase*school*static['urban']])
    rain=xp.log1p(conditions['rain_mm_h']/15)
    columns.extend([rain,rain*static['urban'],rain*static['importance'],rain*static['bridge'],rain**2])
    incident=conditions['incident_pressure']*(.3+.7*static['importance'])/base
    works=conditions['works_pressure']*(.35+.65*static['importance'])/base
    columns.extend(xp.log1p(k*incident) for k in (1,4,12))
    columns.extend(xp.log1p(k*works) for k in (1,2,5))
    columns.append(rain*xp.log1p(4*incident))
    return xp.stack(columns,axis=1)

class NetworkContext:
    def __init__(self,arrays):
        self.arrays=arrays;self.ids=arrays['segment_ids'];self.n=len(self.ids);self._adjacency=None
        if not self.n or not np.all(np.diff(self.ids)>0):raise ValueError('Invalid context IDs')

    def save(self,path):np.savez_compressed(path,**self.arrays)

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as data:return cls({name:data[name] for name in data.files if name!='department'})

    def subset(self,index):
        return {key:self.arrays[key][index] for key in ('urban','tourism','importance','school','activity','bridge')}

    @lru_cache(maxsize=32)
    def influence(self,segment_id,radius_m=1800.):
        if not np.isfinite(radius_m) or not 100<=radius_m<=6000:raise ValueError('Queue extent must be 100..6000 m')
        edge=int(np.searchsorted(self.ids,segment_id))
        if edge>=self.n or self.ids[edge]!=segment_id:raise KeyError('Unknown incident road')
        a=self.arrays['start_nodes'];b=self.arrays['end_nodes'];length=self.arrays['length']
        if self._adjacency is None:
            self._adjacency=[[] for _ in range(int(max(a.max(),b.max()))+1)]
            for i,(u,v,d) in enumerate(zip(a,b,length)):
                self._adjacency[u].append((int(v),float(d)));self._adjacency[v].append((int(u),float(d)))
        distance=np.full(len(self._adjacency),np.inf)
        queue=[]
        for node in (a[edge],b[edge]):
            distance[node]=float(length[edge]/2);heapq.heappush(queue,(float(length[edge]/2),int(node)))
        while queue:
            d,node=heapq.heappop(queue)
            if d>distance[node] or d>radius_m:continue
            for other,cost in self._adjacency[node]:
                candidate=d+cost
                if candidate<distance[other] and candidate<=radius_m:
                    distance[other]=candidate;heapq.heappush(queue,(candidate,other))
        middle=np.minimum(distance[a],distance[b])+length/2;middle[edge]=0
        result=np.where(middle<=radius_m,np.exp(-middle/(radius_m/2)),0).astype(np.float32)
        result.flags.writeable=False
        return result

def pulse(hour,start,end,recovery_hours=2):
    if hour<start or hour>=end+recovery_hours:return 0.
    if hour<end:return float(.65+.35*np.sin(np.pi*(hour-start+.5)/(end-start)))
    return float(.55*np.exp(-(hour-end+.5)/.75))

def validate_conditions(conditions):
    for name in INPUT_NAMES:
        if name not in conditions:raise ValueError('Missing condition '+name)
        values=np.asarray(conditions[name])
        if not np.isfinite(values).all():raise ValueError('Nonfinite condition '+name)
        high=150 if name=='rain_mm_h' else 7 if name=='holiday_code' else 1
        if np.any((values<0)|(values>high)):raise ValueError('Condition outside supported bounds: '+name)
        if name in ('holiday_code','school_break','closed') and np.any(values!=np.floor(values)):
            raise ValueError('Condition must be categorical: '+name)

