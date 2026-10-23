from pathlib import Path
import json
import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

PROJECT=Transformer.from_crs(4326,32616,always_xy=True)
INVERSE=Transformer.from_crs(32616,4326,always_xy=True)
MAJOR={'trunk','trunk_link','primary','primary_link','secondary','secondary_link','tertiary','tertiary_link'}

class Network:
    def __init__(self,folder):
        self.folder=Path(folder)
        if (self.folder/'INCOMPLETE').exists():raise ValueError('La red vial todavía se está preparando.')
        self.manifest=json.loads((self.folder/'manifest.json').read_text(encoding='utf8'))
        if self.manifest['schema']!='navia-car-network-v1':raise ValueError('Formato de red no compatible.')
        for name,shape in self.manifest['array_shapes'].items():
            value=np.load(self.folder/(name+'.npy'),mmap_mode='r',allow_pickle=False)
            if list(value.shape)!=shape:raise ValueError('Red vial incompleta: '+name)
            setattr(self,name,value)
        self.roads=pq.read_table(self.folder/'roads.parquet')
        self.names=self.roads['name'].to_pylist();self.highways=np.asarray(self.roads['highway'].to_pylist())
        self.major=np.isin(self.highways,list(MAJOR));self.lon=self.roads['centroid_lon'].to_numpy();self.lat=self.roads['centroid_lat'].to_numpy()
        self.navigable=np.zeros(len(self.segment_ids),bool);self.navigable[self.part_sid]=True
        self.part_edge_first=np.searchsorted(self.edge_part,np.arange(len(self.part_u)+1)).astype(np.int32)

    def edges_for_part(self,part):
        return np.arange(self.part_edge_first[part],self.part_edge_first[part+1],dtype=np.int32)

    def part_line(self,part):
        return shapely.LineString(self.geometry[self.geometry_first[part]:self.geometry_first[part+1]])

    def snap(self,coordinates):
        if not isinstance(coordinates,(list,tuple)) or len(coordinates)!=2:raise ValueError('Coordenadas de inicio y fin inválidas.')
        try:lon,lat=map(float,coordinates)
        except (ValueError,TypeError):raise ValueError('Coordenadas de inicio y fin inválidas.') from None
        if not np.isfinite([lon,lat]).all() or not (-90.3<=lon<=-87.4 and 12.9<=lat<=14.7):raise ValueError('El punto debe estar en El Salvador.')
        x,y=PROJECT.transform(lon,lat);b=self.part_bbox
        candidates=[]
        for radius in (40,150,500):
            candidates=np.flatnonzero((b[:,0]<=x+radius)&(b[:,2]>=x-radius)&(b[:,1]<=y+radius)&(b[:,3]>=y-radius))
            if len(candidates):
                lines=np.asarray([self.part_line(int(i)) for i in candidates],object)
                distances=shapely.distance(lines,shapely.Point(x,y));j=int(np.argmin(distances))
                if distances[j]<=radius:break
        else:raise ValueError('No hay una carretera vehicular accesible a menos de 500 m del punto.')
        part=int(candidates[j]);line=lines[j];fraction=float(line.project(shapely.Point(x,y))/line.length)
        point=line.interpolate(fraction,normalized=True);ll=INVERSE.transform(point.x,point.y)
        node=int(self.part_u[part]) if fraction<1e-6 else int(self.part_v[part]) if fraction>1-1e-6 else -1
        return {'part':part,'fraction':fraction,'xy':np.array([point.x,point.y]),'coordinates':list(ll),
            'distance_m':float(distances[j]),'node':node}

    def clipped_geometry(self,part,reverse=False,start_fraction=0.,end_fraction=1.):
        from shapely.ops import substring
        line=self.part_line(part)
        start,end=(1-start_fraction,1-end_fraction) if reverse else (start_fraction,end_fraction)
        piece=substring(line,start,end,normalized=True)
        xy=shapely.get_coordinates(piece)
        if len(xy)==1:xy=np.vstack((xy,xy))
        lon,lat=INVERSE.transform(xy[:,0],xy[:,1])
        return np.round(np.c_[lon,lat],6).tolist()
