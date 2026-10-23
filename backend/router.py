from datetime import timedelta
import math,time
import numpy as np
import shapely
from .kernel import earliest,edge_time
from .network import PROJECT

class NoRouteError(ValueError):pass

class Router:
    def __init__(self,network,traffic):self.n=network;self.traffic=traffic

    def endpoints(self,start,end):
        n=self.n
        if start['node']>=0:
            a=start['node'];initial=n.node_edges[n.node_first[a]:n.node_first[a+1]].copy()
            initial_lengths=n.part_length[n.edge_part[initial]].astype(float)
        else:
            initial=n.edges_for_part(start['part'])
            initial_lengths=np.array([float(n.part_length[start['part']])*(start['fraction'] if n.edge_reverse[e] else 1-start['fraction']) for e in initial])
        goals=n.edges_for_part(end['part'])
        goal_lengths=np.array([float(n.part_length[end['part']])*(1-end['fraction'] if n.edge_reverse[e] else end['fraction']) for e in goals])
        return initial,initial_lengths,goals,goal_lengths

    def search(self,start,end,model,speeds,start_seconds,blocked,bound=math.inf):
        n=self.n;initial,initial_lengths,goals,goal_lengths=self.endpoints(start,end)
        direct=None;best_bound=bound
        for i,edge in enumerate(initial):
            for j,goal in enumerate(goals):
                if edge!=goal or blocked[n.edge_part[edge]]:continue
                offset=float(n.part_length[n.edge_part[edge]])-initial_lengths[i]
                if goal_lengths[j]+1e-7>=offset:
                    length=max(0.,goal_lengths[j]-offset);seconds=float(edge_time(length,start_seconds,int(n.part_sid[n.edge_part[edge]]),speeds))
                    if seconds<best_bound:best_bound=seconds;direct=(int(edge),float(offset),float(goal_lengths[j]))
        scale=float(n.manifest['heuristic_metric_scale']);goal_xy=end['xy'].copy()
        if end['node']>=0:goal_xy=np.array(n.node_xy[end['node']])
        else:
            for edge,length in zip(goals,goal_lengths):
                xy=n.node_xy[n.edge_from[edge]];straight=np.linalg.norm(xy-goal_xy)
                if straight>1e-6:scale=min(scale,float(length/straight)*.999)
        result=earliest(n.state_edge,n.state_first,n.state_next,n.edge_to,n.edge_part,n.part_sid,n.part_length,n.node_xy,
            initial,initial_lengths,goals,goal_lengths,int(end['node']),goal_xy,speeds,start_seconds,blocked,
            scale/(float(model.free_speed.max())/3.6),best_bound)
        previous,terminal,terminal_edge,seconds,visits,finished=result
        if not finished:raise NoRouteError('La búsqueda superó el límite de cálculo. Prueba otros puntos cercanos.')
        if terminal<0:
            if direct is None:return None
            edge,left,right=direct;parts=[(edge,left,right)]
        else:
            states=[];current=terminal
            while current>=0:
                states.append(int(current));current=int(previous[current])
            states.reverse();edges=[int(n.state_edge[s]) for s in states]
            parts=[]
            for i,edge in enumerate(edges):
                length=float(n.part_length[n.edge_part[edge]])
                offset=length-float(initial_lengths[np.flatnonzero(initial==edge)[0]]) if i==0 else 0.
                parts.append((edge,offset,length))
            if terminal_edge>=0:
                j=int(np.flatnonzero(goals==terminal_edge)[0]);parts.append((int(terminal_edge),0.,float(goal_lengths[j])))
        # Ignore zero-distance endpoint pieces when presenting travel geometry.
        parts=[p for p in parts if p[2]-p[1]>1e-5]
        if not parts:return None
        return {'parts':parts,'seconds':float(seconds),'visits':int(visits)}

    def evaluate(self,path,model,speeds,start_seconds,departure):
        n=self.n;elapsed=0.;distance=0.;free_seconds=0.;coordinates=[];pieces=[];physical={};traffic_distance=np.zeros(3)
        for edge,left,right in path['parts']:
            part=int(n.edge_part[edge]);row=int(n.part_sid[part]);length=right-left
            at=start_seconds+elapsed;duration=float(edge_time(length,at,row,speeds))
            entry=departure+timedelta(seconds=elapsed);elapsed+=duration;exit=departure+timedelta(seconds=elapsed)
            fraction_start=left/n.part_length[part];fraction_end=right/n.part_length[part]
            geo=n.clipped_geometry(part,bool(n.edge_reverse[edge]),float(fraction_start),float(fraction_end))
            if coordinates and coordinates[-1]==geo[0]:coordinates.extend(geo[1:])
            else:coordinates.extend(geo)
            speed=float(speeds[int(at//3600),row]*3.6)
            factor=float(model.free_speed[row]/speed) if speed>0 else math.inf
            level=int(factor>=model.thresholds[0])+int(factor>=model.thresholds[1])
            physical[int(n.segment_ids[row])]=physical.get(int(n.segment_ids[row]),0.)+length
            traffic_distance[level]+=length;free_seconds+=length*3.6/float(model.free_speed[row]);distance+=length
            pieces.append({'segment_id':int(n.segment_ids[row]),'name':n.names[row] or 'Vía sin nombre',
                'entry_local':entry.isoformat(),'exit_local':exit.isoformat(),'travel_time_s':round(duration,3),
                'length_m':round(length,2),'speed_at_entry_kmh':round(speed,1),'traffic_level':level,'geometry':geo})
        if abs(elapsed-path['seconds'])>.01:raise RuntimeError('La evaluación temporal no coincide con la búsqueda.')
        xy=np.array(PROJECT.transform(*np.array(coordinates).T)).T
        return {'travel_time_s':elapsed,'distance_km':distance/1000,'minutes':max(1,math.ceil(elapsed/60)),
            'arrival_local':(departure+timedelta(seconds=elapsed)).isoformat(),'departure_local':departure.isoformat(),
            'delay_minutes':round(max(0,elapsed-free_seconds)/60,1),'geometry':coordinates,'segments':pieces,
            'traffic_km':{key:round(float(value)/1000,3) for key,value in zip(('normal','moderate','jam'),traffic_distance)},
            '_physical':physical,'_line':shapely.LineString(xy),'_path':path}

    @staticmethod
    def diversity(candidate,existing):
        denominator=min(sum(candidate['_physical'].values()),sum(existing['_physical'].values()))
        common=sum(min(value,existing['_physical'].get(key,0.)) for key,value in candidate['_physical'].items())
        overlap=common/max(1,denominator)
        line=candidate['_line'];points=shapely.line_interpolate_point(line,np.linspace(0,line.length,101))
        separation=shapely.distance(points,existing['_line']);threshold=min(400,max(60,denominator*.03))
        return overlap,float(np.mean(separation>=threshold)),float(np.quantile(separation,.9))

    def routes(self,start_coordinates,end_coordinates,departure,model,signature,limit=3):
        began=time.perf_counter();n=self.n;start=n.snap(start_coordinates);end=n.snap(end_coordinates)
        if np.linalg.norm(start['xy']-end['xy'])<2:raise ValueError('Selecciona un inicio y un destino distintos.')
        speeds,start_seconds=self.traffic.profile(model,signature,departure)
        blocked=n.part_destination.copy()
        access_ways=n.part_way[[start['part'],end['part']]]
        blocked[np.isin(n.part_way,access_ways)]=False
        best_path=self.search(start,end,model,speeds,start_seconds,blocked)
        if best_path is None:raise NoRouteError('No hay una conexión vehicular disponible entre estos puntos. Prueba una vía cercana.')
        best=self.evaluate(best_path,model,speeds,start_seconds,departure);selected=[best];candidates=[];seen={tuple(e for e,_,_ in best_path['parts'])}
        maximum=best['travel_time_s']+min(900,best['travel_time_s']*.30)
        line=best['_line'];mid=(n.part_bbox[:,:2]+n.part_bbox[:,2:])/2
        # Block separate geographic patches, rather than perturbing a time weight.
        # All candidate searches still use physical arrival time and legal turns.
        if limit>1 and line.length>500:
            for fraction,radius_scale in ((.5,1),(.3,1),(.7,1),(.5,.5),(.2,.6),(.8,.6),(.4,1.5),(.6,1.5)):
                point=line.interpolate(fraction,normalized=True)
                radius=min(2500,max(100,line.length*.025))*radius_scale
                extra=blocked.copy();extra|=(mid[:,0]-point.x)**2+(mid[:,1]-point.y)**2<radius**2
                extra[[start['part'],end['part']]]=False
                candidate_path=self.search(start,end,model,speeds,start_seconds,extra,maximum+1e-6)
                if candidate_path is None or candidate_path['seconds']>maximum:continue
                key=tuple(e for e,_,_ in candidate_path['parts'])
                if key in seen:continue
                seen.add(key);candidate=self.evaluate(candidate_path,model,speeds,start_seconds,departure)
                if any(self.diversity(candidate,other)[0]>.80 or self.diversity(candidate,other)[1]<.12 for other in selected):continue
                candidates.append(candidate)
            candidates.sort(key=lambda route:route['travel_time_s'])
            for candidate in candidates:
                if all(self.diversity(candidate,other)[0]<=.80 and self.diversity(candidate,other)[1]>=.12 for other in selected):
                    selected.append(candidate)
                if len(selected)>=limit:break
        output=[]
        for i,route in enumerate(selected):
            overlap,separated,p90=self.diversity(route,best) if i else (1.,0.,0.)
            route.update(name='Más rápida' if i==0 else f'Alternativa {i}',tag='Mejor tiempo previsto' if i==0 else 'Recorrido diferente',
                shared_with_fastest=round(overlap,3),separation_p90_m=round(p90,1))
            output.append({key:value for key,value in route.items() if not key.startswith('_')})
        return {'routes':output,'model':model.name,'departure_local':departure.isoformat(),
            'snapped':{'start':start['coordinates'],'end':end['coordinates'],'start_distance_m':round(start['distance_m'],1),'end_distance_m':round(end['distance_m'],1)},
            'alternative_policy':{'maximum_extra_ratio':.30,'maximum_extra_seconds':900,'maximum_shared_fraction':.80,'minimum_separated_fraction':.12},
            'message':f'{len(output)} ruta'+('s eficientes' if len(output)>1 else ' eficiente; no se encontraron alternativas suficientemente distintas y rápidas.'),
            'elapsed_seconds':round(time.perf_counter()-began,3),'synthetic':True,'execution':'CPU'}
