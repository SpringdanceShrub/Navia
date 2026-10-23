import heapq,math
import numpy as np
from numba import njit

@njit(cache=True,nogil=True)
def edge_time(length,at,segment,speeds):
    start=at;remaining=length
    while remaining>1e-7:
        slot=int(at//3600)
        if slot<0 or slot>=len(speeds):return math.inf
        seconds=(slot+1)*3600-at;rate=speeds[slot,segment]
        if rate>0 and remaining<=rate*seconds:return at-start+remaining/rate
        remaining-=rate*seconds;at+=seconds
    return at-start

@njit(cache=True,nogil=True)
def earliest(state_edge,state_first,state_next,edge_to,edge_part,part_sid,part_length,node_xy,
             initial_edges,initial_lengths,goal_edges,goal_lengths,goal_node,goal_xy,
             speeds,start_seconds,blocked,heuristic_rate,bound,max_visits=2000000):
    distance=np.full(len(state_edge),np.inf);previous=np.full(len(state_edge),-2,np.int32)
    queue=[(0.,0.,0)];queue.pop();best=bound;terminal=-1;terminal_edge=-2;visits=0
    for j in range(len(initial_edges)):
        edge=initial_edges[j];part=edge_part[edge]
        if blocked[part]:continue
        cost=edge_time(initial_lengths[j],start_seconds,part_sid[part],speeds)
        if cost<distance[edge]:
            distance[edge]=cost;previous[edge]=-1;v=edge_to[edge]
            h=math.hypot(node_xy[v,0]-goal_xy[0],node_xy[v,1]-goal_xy[1])*heuristic_rate
            heapq.heappush(queue,(cost+h,cost,np.int64(edge)))
    while queue:
        priority,cost,state=heapq.heappop(queue)
        if priority>=best-1e-9:break
        if cost>distance[state]+1e-9:continue
        visits+=1
        if visits>max_visits:return previous,terminal,terminal_edge,best,visits,False
        edge=state_edge[state];node=edge_to[edge]
        if goal_node>=0 and node==goal_node:best=cost;terminal=state;terminal_edge=-1;continue
        for position in range(state_first[state],state_first[state+1]):
            target=state_next[position];other=state_edge[target];part=edge_part[other]
            if blocked[part]:continue
            segment=part_sid[part]
            for j in range(len(goal_edges)):
                if other==goal_edges[j]:
                    ending=cost+edge_time(goal_lengths[j],start_seconds+cost,segment,speeds)
                    if ending<best:best=ending;terminal=state;terminal_edge=other
            value=cost+edge_time(part_length[part],start_seconds+cost,segment,speeds)
            if value+1e-9<distance[target]:
                distance[target]=value;previous[target]=state;v=edge_to[other]
                h=math.hypot(node_xy[v,0]-goal_xy[0],node_xy[v,1]-goal_xy[1])*heuristic_rate
                if value+h<best:heapq.heappush(queue,(value+h,value,np.int64(target)))
    return previous,terminal,terminal_edge,best,visits,True
