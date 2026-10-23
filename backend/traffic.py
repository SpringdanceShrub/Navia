from collections import OrderedDict
from datetime import datetime,timedelta
from threading import RLock
import numpy as np
import shapely
from .verdict.costs import LOCAL
from .verdict.calendar import holiday_context

def local_datetime(value=None):
    if value is None:return datetime.now(LOCAL).replace(second=0,microsecond=0)
    if not isinstance(value,str) or len(value)>40:raise ValueError('La salida debe ser una fecha y hora válidas.')
    try:at=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('La salida debe ser una fecha y hora válidas.') from None
    if not 2020<=at.year<=2100:raise ValueError('La fecha está fuera del intervalo permitido.')
    return at.replace(tzinfo=LOCAL) if at.tzinfo is None else at.astimezone(LOCAL)

class TrafficService:
    def __init__(self,network):self.network=network;self.cache=OrderedDict();self.lock=RLock()

    def profile(self,model,signature,departure,hours=48):
        origin=departure.replace(minute=0,second=0,microsecond=0);values=[]
        with self.lock:
            for hour in range(hours):
                at=origin+timedelta(hours=hour);key=(signature,at.isoformat())
                if key not in self.cache:
                    if holiday_context(at.date())['holiday']=='none':
                        speed=(model.free_speed/np.exp(model.log_delay[at.weekday(),at.hour])/3.6).astype(np.float32)
                    else:speed=(model.predict_at(model.ids,at)['speed_kmh']/3.6).astype(np.float32)
                    self.cache[key]=speed
                self.cache.move_to_end(key);values.append(self.cache[key])
                while len(self.cache)>48:self.cache.popitem(last=False)
        return np.stack(values),float((departure-origin).total_seconds())

    def view(self,model,bbox,zoom,at):
        if len(bbox)!=4 or not np.isfinite(bbox).all():raise ValueError('Límites del mapa inválidos.')
        west,south,east,north=bbox
        if not (-180<=west<east<=180 and -85<=south<north<=85):raise ValueError('Límites del mapa inválidos.')
        n=self.network;locals_visible=zoom>=16
        use=(n.lon>=west-.004)&(n.lon<=east+.004)&(n.lat>=south-.004)&(n.lat<=north+.004)&n.navigable
        if not locals_visible:use&=n.major
        if zoom<10:use&=np.isin(n.highways,['trunk','trunk_link','primary','primary_link'])
        index=np.flatnonzero(use);truncated=len(index)>9000
        if truncated:
            # Preserve the most useful road hierarchy at a wide view; never randomize colors.
            rank=np.array([0 if n.highways[i].startswith('trunk') else 1 if n.highways[i].startswith('primary') else 2 if n.highways[i].startswith('secondary') else 3 for i in index])
            index=np.sort(index[np.argsort(rank,kind='stable')[:9000]])
        prediction=model.predict_at(n.segment_ids[index],at) if len(index) else None
        geometry=shapely.from_wkb(n.roads['geometry_wkb'].take(index).to_pylist())
        tolerance=.00012 if zoom<12 else .000035 if zoom<15 else .000005
        geometry=shapely.simplify(geometry,tolerance,preserve_topology=True)
        features=[]
        for j,(row,line) in enumerate(zip(index,geometry)):
            features.append({'type':'Feature','geometry':{'type':'LineString','coordinates':np.round(shapely.get_coordinates(line),6).tolist()},
                'properties':{'segment_id':int(n.segment_ids[row]),'name':n.names[row] or 'Vía sin nombre',
                    'level':int(prediction['traffic_level'][j]),'speed_kmh':round(float(prediction['speed_kmh'][j]),1),
                    'delay_factor':round(float(prediction['delay_factor'][j]),3),'local':not bool(n.major[row])}})
        return {'type':'FeatureCollection','features':features,'at':at.isoformat(),'model':model.name,
            'locals_visible':locals_visible,'truncated':truncated,'synthetic':True}
