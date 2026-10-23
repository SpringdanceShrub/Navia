import numpy as np
from .features import HOLIDAYS,pulse

def case_conditions(context,case,hour,index=None):
    index=np.arange(context.n) if index is None else np.asarray(index)
    size=len(index);kind=case['kind'];holiday=case.get('holiday','none')
    code=HOLIDAYS.index(holiday)
    scope=np.ones(size,np.float32)
    distance=None
    if code==7 or kind in ('rain_moderate','rain_heavy','combined'):
        distance=np.hypot(context.arrays['x'][index]-case['center_x'],context.arrays['y'][index]-case['center_y'])
    if code==7:
        radius=case['holiday_locality_radius_m'];scope=np.where(distance<radius*3,np.exp(-.5*(distance/radius)**2),0).astype(np.float32)
    rain=np.zeros(size,np.float32)
    if kind in ('rain_moderate','rain_heavy','combined'):
        radius=case['storm_radius_m'];spatial=np.where(distance<3*radius,np.exp(-.5*(distance/radius)**2),0)
        temporal=np.exp(-.5*((hour+.5-case['rain_center_hour'])/case['rain_width_hours'])**2)
        rain=(case['rain_peak_mm_h']*temporal*spatial).astype(np.float32);rain[rain<.2]=0
    pressure=np.zeros(size,np.float32);works=np.zeros(size,np.float32);closed=np.zeros(size,bool)
    if kind in ('incident','closure','roadworks','combined'):
        network=context.influence(int(case['incident_target_id']),float(case['queue_extent_m']))[index]
        p=network*case['severity']*pulse(hour,case['start_hour'],case['end_hour'],case['recovery_hours'])
        if kind=='roadworks':works=p.astype(np.float32)
        else:pressure=p.astype(np.float32)
        if kind=='closure' and case['start_hour']<=hour<case['end_hour']:
            closed=context.ids[index]==case['incident_target_id']
    return {'rain_mm_h':rain,'holiday_code':np.full(size,code,np.int8),'holiday_scope':scope,
        'school_break':np.full(size,case.get('school_break',False),bool),
        'incident_pressure':pressure,'works_pressure':works,'closed':closed}
