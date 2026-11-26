"use strict";
const map=L.map('map',{zoomControl:false,preferCanvas:true,tapHold:false}).setView([13.6929,-89.2182],12);
const offlineBasemap=L.maplibreGL({style:'/api/offline-map/style.json',interactive:false,
  attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · BBBike · <a href="https://www.naturalearthdata.com/about/terms-of-use/">Natural Earth</a> · <a href="https://www.geoboundaries.org/api/current/gbOpen/SLV/ADM1/">geoBoundaries</a>'
}).addTo(map);
offlineBasemap.getMaplibreMap().on('error',event=>console.error('Mapa local:',event.error?.message||event.error));
L.control.zoom({position:'bottomleft'}).addTo(map);

map.createPane('trafficPane').style.zIndex=410;
map.createPane('routePane').style.zIndex=450;
const routeRenderer=L.canvas({pane:'routePane',padding:.3});
const trafficRenderer=L.canvas({pane:'trafficPane',padding:.3});
const routeTrafficColors=['#9470cf','#7243b4','#4d1c79'];
const routeSolidColors=['#a88be8','#d1a8db','#8e9ed9'];
const ids=['searchInput','searchResults','clearSearch','searchSubmit','startInput','endInput','pickStart','pickEnd','selectionHint','departureLabel','departureHour','departureMinute','departurePeriod','calculateBtn','routesList','routeCount','routeLegend','resetBtn','statusPill','statusText','modelSelect','modelIcon','normalMode','trafficMode','trafficLegend','trafficTime','panelToggle','plannerBody'];
const els=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)]));
const localFormat=new Intl.DateTimeFormat('es-SV',{timeZone:'America/El_Salvador',weekday:'long',hour:'2-digit',minute:'2-digit',hour12:true});
let departureDate,searchMarker=null;
let activeSelection=null,startPoint=null,endPoint=null,startMarker=null,endMarker=null,routeLayers=[],currentRoutes=[];
let routeTimeBubble=null;
let selectedRoute=0,busy=false,models=[],modelFingerprint='',trafficEnabled=false,trafficLayer=null;
let autoRouteEnabled=false,routeRecalcTimer=null,routeRecalcQueued=false,routeRequestActive=false;
let routeController=null,routeEpoch=0,trafficController=null,trafficEpoch=0,searchController=null,searchEpoch=0,searchTimer=null,trafficTimer=null;
let trafficRequestKey=null,trafficLoadedKey=null,trafficMoving=false;
let demoRunning=false;
const trafficViewCache=new Map();
function status(text){els.statusText.textContent=text||'';els.statusPill.classList.toggle('hidden',!text);}
function escapeHtml(value){return String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');}
function departure(){
  if(![els.departureHour,els.departureMinute].every(input=>input.validity.valid))return null;
  const hour=Number(els.departureHour.value)%12+(els.departurePeriod.textContent==='PM'?12:0);
  return `${departureDate}T${String(hour).padStart(2,'0')}:${els.departureMinute.value.padStart(2,'0')}:00-06:00`;
}
function localNow(){const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/El_Salvador',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;}
function setDeparture(value){
  const [date,time]=value.split('T'),[hour,minute]=time.split(':').map(Number);
  departureDate=date;
  els.departureHour.value=String(hour%12||12).padStart(2,'0');
  els.departureMinute.value=String(minute).padStart(2,'0');
  els.departurePeriod.textContent=hour>=12?'PM':'AM';
}
setDeparture(localNow());
function renderDeparture(){
  els.departureLabel.textContent=new Intl.DateTimeFormat('es-SV',{timeZone:'America/El_Salvador',weekday:'long'}).format(new Date(`${departureDate}T12:00:00-06:00`));
  els.departurePeriod.setAttribute('aria-label',`Cambiar a ${els.departurePeriod.textContent==='AM'?'PM':'AM'}`);
  for(const input of [els.departureHour,els.departureMinute]){
    if(input.validity.valid)input.setAttribute('aria-valuenow',String(Number(input.value)));
    else input.removeAttribute('aria-valuenow');
    input.setAttribute('aria-invalid',String(!input.validity.valid));
  }
}
function ready(){els.calculateBtn.disabled=busy||!startPoint||!endPoint||!els.modelSelect.value||!departure();}
function clearRoutes(){routeTimeBubble?.remove();routeTimeBubble=null;routeLayers.forEach(line=>map.removeLayer(line));routeLayers=[];currentRoutes=[];els.routeCount.textContent='0';els.routeLegend.classList.add('hidden');els.routesList.replaceChildren();const div=document.createElement('div');div.className='empty-route';div.textContent='Selecciona inicio y destino para calcular rutas.';els.routesList.append(div);}
function invalidateRoutes(){
  routeEpoch++;clearTimeout(routeRecalcTimer);routeRecalcTimer=null;routeRecalcQueued=false;
  busy=routeRequestActive;
  if(!busy)els.calculateBtn.textContent='Calcular rutas';
  clearRoutes();ready();
}
function scheduleRouteRecalculation(){
  if(!autoRouteEnabled||!startPoint||!endPoint||!els.modelSelect.value||!departure())return;
  routeRecalcTimer=setTimeout(()=>{routeRecalcTimer=null;calculateRoutes({automatic:true});},650);
}
function clearSelection(){activeSelection=null;for(const [kind,el] of [['start',els.pickStart],['end',els.pickEnd]]){el.classList.remove('active');el.setAttribute('aria-pressed','false');}els.selectionHint.textContent=startPoint&&endPoint?'Inicio y destino listos.':'Elige inicio y destino en el mapa.';map.getContainer().classList.remove('picking');}
function choose(kind){dismissMapActions();activeSelection=kind;els.pickStart.classList.toggle('active',kind==='start');els.pickEnd.classList.toggle('active',kind==='end');els.pickStart.setAttribute('aria-pressed',String(kind==='start'));els.pickEnd.setAttribute('aria-pressed',String(kind==='end'));els.selectionHint.textContent=kind==='start'?'Haz clic en el mapa para elegir el inicio.':'Haz clic en el mapa para elegir el destino.';map.getContainer().classList.add('picking');}
function icon(kind){return L.divIcon({className:'place-marker endpoint-marker',html:`<span><b>${kind==='start'?'I':'F'}</b></span>`,iconSize:[24,32],iconAnchor:[12,32]});}
let mapClickTimer=null,temporaryMarker=null,interactionPopup=null,temporaryCoordinates=null;
function dismissMapActions(){
  clearTimeout(mapClickTimer);mapClickTimer=null;
  const popup=interactionPopup;interactionPopup=null;popup?.remove();
  temporaryMarker?.remove();temporaryMarker=null;
  if(temporaryCoordinates&&els.statusText.textContent===temporaryCoordinates)status('');
  temporaryCoordinates=null;
}
function actionContent(title,actions){
  const content=document.createElement('div');content.className='map-actions';
  const heading=document.createElement('strong');heading.className='map-actions-title';heading.textContent=title;content.append(heading);
  for(const action of actions){
    const button=document.createElement('button');button.type='button';button.className='map-action';
    if(action.danger)button.classList.add('map-action-delete');
    button.textContent=action.label;
    button.addEventListener('click',event=>{event.stopPropagation();dismissMapActions();action.run();});
    content.append(button);
  }
  L.DomEvent.disableClickPropagation(content);L.DomEvent.disableScrollPropagation(content);
  return content;
}
function mapPlaceAt(point){
  const gl=offlineBasemap.getMaplibreMap();
  if(!gl.isStyleLoaded())return null;
  const layers=['poi-dots','poi-labels'].filter(id=>gl.getLayer(id));
  if(!layers.length)return null;
  const screen=gl.project([point.lng,point.lat]);
  const hits=gl.queryRenderedFeatures([[screen.x-8,screen.y-8],[screen.x+8,screen.y+8]],{layers});
  let nearest=null,bestDistance=Infinity;
  for(const feature of hits){
    const name=String(feature.properties?.name_es||feature.properties?.name||'').trim();
    if(!name||feature.geometry?.type!=='Point')continue;
    const [lon,lat]=feature.geometry.coordinates;
    if(!Number.isFinite(lon)||!Number.isFinite(lat))continue;
    const anchor=gl.project([lon,lat]),distance=(anchor.x-screen.x)**2+(anchor.y-screen.y)**2;
    if(distance<bestDistance){bestDistance=distance;nearest={name,point:L.latLng(lat,lon)};}
  }
  return nearest;
}
function addTemporaryMarker(point,name=null){
  temporaryMarker=L.marker(point,{
    icon:L.divIcon({className:'temporary-marker',html:'<span></span>',iconSize:[18,18],iconAnchor:[9,9]}),
    interactive:true,keyboard:true,bubblingMouseEvents:false,
    title:name||'Punto temporal: elegir inicio o destino',alt:name||'Punto temporal'
  }).addTo(map);
  temporaryMarker.on('click',()=>showPointChoices(point,name));
  temporaryMarker.on('contextmenu',event=>{
    if(event.originalEvent)L.DomEvent.stop(event.originalEvent);
    showPointChoices(point,name);
  });
}
function showTemporaryCoordinates(point,name=null){
  dismissMapActions();addTemporaryMarker(point,name);
  const p=L.latLng(point);
  temporaryCoordinates=name||`${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`;
  status(temporaryCoordinates);
}
function showMapActions(point,title,actions,temporary=false,popupOptions={}){
  dismissMapActions();
  if(temporary)addTemporaryMarker(point,title==='Elegir punto'?null:title);
  const popup=L.popup({className:'map-action-popup',autoPan:false,closeOnClick:false,autoClose:false,offset:[0,-12],...popupOptions})
    .setLatLng(point).setContent(actionContent(title,actions));
  interactionPopup=popup;
  popup.on('remove',()=>{
    if(interactionPopup!==popup)return;
    interactionPopup=null;temporaryMarker?.remove();temporaryMarker=null;
  });
  popup.openOn(map);
}
function removePoint(kind){
  dismissMapActions();
  if(kind==='start'){startMarker?.remove();startMarker=startPoint=null;els.startInput.value='';}
  else{endMarker?.remove();endMarker=endPoint=null;els.endInput.value='';}
  invalidateRoutes();clearSelection();status('');
}
function setPoint(kind,point,label){
  dismissMapActions();
  const p=L.latLng(point);invalidateRoutes();
  const marker=L.marker(p,{icon:icon(kind),draggable:true,bubblingMouseEvents:false,
    title:kind==='start'?'Inicio':'Destino'});
  if(kind==='start'){
    startPoint=p;startMarker?.remove();startMarker=marker;els.startInput.value=label||`${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`;
  }else{
    endPoint=p;endMarker?.remove();endMarker=marker;els.endInput.value=label||`${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`;
  }
  marker.on('dragstart',dismissMapActions);
  marker.on('dragend',()=>setPoint(kind,marker.getLatLng()));
  marker.on('contextmenu',event=>{
    if(event.originalEvent)L.DomEvent.stop(event.originalEvent);
    showMapActions(marker.getLatLng(),kind==='start'?'Inicio':'Destino',[
      {label:kind==='start'?'Eliminar inicio':'Eliminar destino',danger:true,run:()=>removePoint(kind)}
    ]);
  });
  nextHoldPoint=kind==='start'?'end':'start';
  marker.addTo(map);clearSelection();ready();
}

let mapPress=null,pressedMapClick=null,nextHoldPoint='start';
function cancelMapPress(){
  if(mapPress)clearTimeout(mapPress.timer);
  mapPress=null;
}
map.getContainer().addEventListener('pointerdown',event=>{
  cancelMapPress();pressedMapClick=null;
  if(event.button!==0||event.isPrimary===false)return;
  if(event.target.closest('.leaflet-popup, .leaflet-control, .leaflet-marker-icon:not(.temporary-marker)'))return;
  const press={pointerId:event.pointerId,pointerType:event.pointerType,x:event.clientX,y:event.clientY,
    point:map.mouseEventToLatLng(event),timer:null};
  mapPress=press;
  press.timer=setTimeout(()=>{
    if(mapPress!==press)return;
    press.timer=null;
    pressedMapClick={x:press.x,y:press.y,pointerType:press.pointerType};
    const kind=activeSelection||(!startPoint?'start':!endPoint?'end':nextHoldPoint);
    const place=mapPlaceAt(press.point);
    setPoint(kind,place?.point||press.point,place?.name);
    status(kind==='start'?'Inicio marcado':'Destino marcado');
  },650);
});
window.addEventListener('pointermove',event=>{
  if(mapPress?.pointerId===event.pointerId&&Math.hypot(event.clientX-mapPress.x,event.clientY-mapPress.y)>8)cancelMapPress();
},true);
for(const type of ['pointerup','pointercancel'])window.addEventListener(type,event=>{
  if(mapPress?.pointerId===event.pointerId)cancelMapPress();
},true);

map.getContainer().addEventListener('click',event=>{
  if(!pressedMapClick||event.detail===0)return;
  if(Math.hypot(event.clientX-pressedMapClick.x,event.clientY-pressedMapClick.y)>16)return;
  pressedMapClick=null;event.preventDefault();event.stopImmediatePropagation();
},true);
map.on('movestart zoomstart dragstart dblclick',cancelMapPress);
window.addEventListener('blur',cancelMapPress);
function showPointChoices(point,name=null){
  showMapActions(point,name||'Elegir punto',[
    {label:'Marcar como Inicio',run:()=>setPoint('start',point,name)},
    {label:'Marcar como Destino',run:()=>setPoint('end',point,name)}
  ],true);
}
map.on('click',event=>{
  dismissMapActions();
  if(event.originalEvent?.detail>1)return;
  const selection=activeSelection;
  mapClickTimer=setTimeout(()=>{
    mapClickTimer=null;
    const place=mapPlaceAt(event.latlng),point=place?.point||event.latlng;
    if(selection&&selection===activeSelection)setPoint(selection,point,place?.name);
    else showTemporaryCoordinates(point,place?.name);
  },350);
});
map.on('contextmenu',event=>{
  if(event.originalEvent)L.DomEvent.stop(event.originalEvent);
  if(mapPress?.pointerType==='touch'||pressedMapClick?.pointerType==='touch')return;
  const place=mapPlaceAt(event.latlng);
  showPointChoices(place?.point||event.latlng,place?.name);
});
map.on('movestart zoomstart dragstart dblclick',dismissMapActions);
map.getContainer().addEventListener('contextmenu',event=>event.preventDefault());
window.addEventListener('blur',dismissMapActions);
window.addEventListener('resize',dismissMapActions);
document.addEventListener('pointerdown',event=>{
  if(!event.target.closest('.map-action-popup, .temporary-marker'))dismissMapActions();
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){cancelMapPress();dismissMapActions();searchMarker?.closePopup();}
});
els.pickStart.addEventListener('click',()=>choose('start'));els.pickEnd.addEventListener('click',()=>choose('end'));
els.panelToggle.addEventListener('click',()=>{const expanded=els.panelToggle.getAttribute('aria-expanded')==='true';els.panelToggle.setAttribute('aria-expanded',String(!expanded));els.panelToggle.setAttribute('aria-label',expanded?'Abrir planificador':'Contraer planificador');els.panelToggle.textContent=expanded?'+':'−';els.plannerBody.hidden=expanded;});
async function loadModels(){try{const response=await fetch('/api/models');const data=await response.json();if(!response.ok)throw new Error(data.error||'No se pudieron detectar los modelos.');const fingerprint=JSON.stringify(data.models);if(fingerprint===modelFingerprint)return;const previous=els.modelSelect.value;const previousRevision=models.find(m=>m.id===previous)?.revision;models=data.models;els.modelSelect.replaceChildren();for(const model of models){const option=document.createElement('option');option.value=model.id;option.textContent=model.name;els.modelSelect.append(option);}if(models.some(m=>m.id===previous)){els.modelSelect.value=previous;if(previousRevision!==models.find(m=>m.id===previous)?.revision){invalidateRoutes();clearTraffic();}}else{let remembered;try{remembered=localStorage.getItem('navia-model');}catch{}if(models.some(m=>m.id===remembered))els.modelSelect.value=remembered;invalidateRoutes();}modelFingerprint=fingerprint;if(!models.length){const option=document.createElement('option');option.value='';option.textContent='Sin modelos instalados';els.modelSelect.append(option);status('Añade un modelo compatible a la carpeta models.');}else{els.modelIcon.src=models.find(m=>m.id===els.modelSelect.value)?.icon||'/frontend/assets/Verdict.svg';scheduleTraffic();}ready();}catch(error){status(error.message);}}
els.modelSelect.addEventListener('change',()=>{try{localStorage.setItem('navia-model',els.modelSelect.value);}catch{}invalidateRoutes();clearTraffic();status('');scheduleTraffic();});
window.addEventListener('focus',loadModels);setInterval(()=>{if(!document.hidden)loadModels();},15000);
function departureChanged(){renderDeparture();invalidateRoutes();clearTraffic();scheduleTraffic();status('');scheduleRouteRecalculation();}
function stepDeparture(minutes=0,days=0){
  const value=departure();
  if(!value){[els.departureHour,els.departureMinute].find(input=>!input.validity.valid)?.reportValidity();return;}
  const shifted=new Date(new Date(value.slice(0,19)+'Z').getTime()+(minutes+days*1440)*60000);
  const next=shifted.toISOString().slice(0,16);
  if(next<'2020-01-01T00:00'||next>'2100-12-31T23:59')return;
  setDeparture(next);departureChanged();
}
for(const [input,step] of [[els.departureHour,60],[els.departureMinute,1]]){
  input.addEventListener('input',departureChanged);
  input.addEventListener('focus',()=>input.select());
  input.addEventListener('change',()=>{if(input.validity.valid)input.value=input.value.padStart(2,'0');renderDeparture();});
  input.addEventListener('keydown',event=>{if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();stepDeparture(event.key==='ArrowUp'?step:-step);}});
}
document.querySelectorAll('[data-minutes]').forEach(button=>button.addEventListener('click',()=>stepDeparture(Number(button.dataset.minutes))));
document.querySelectorAll('[data-days]').forEach(button=>button.addEventListener('click',()=>stepDeparture(0,Number(button.dataset.days))));
els.departurePeriod.addEventListener('click',()=>{els.departurePeriod.textContent=els.departurePeriod.textContent==='AM'?'PM':'AM';departureChanged();});
function duration(seconds){const minutes=Math.max(1,Math.ceil(seconds/60));return minutes>=60?`${Math.floor(minutes/60)} h ${minutes%60} min`:`${minutes} min`;}
function routeMidpoint(geometry){
  if(!geometry?.length)return null;
  const points=geometry.map(([lon,lat])=>L.latLng(lat,lon));
  const lengths=points.slice(1).map((point,i)=>points[i].distanceTo(point));
  let remaining=lengths.reduce((sum,length)=>sum+length,0)/2;
  for(let i=0;i<lengths.length;i++){
    if(remaining<=lengths[i]&&lengths[i]>0){
      const fraction=remaining/lengths[i],a=points[i],b=points[i+1];
      return L.latLng(a.lat+(b.lat-a.lat)*fraction,a.lng+(b.lng-a.lng)*fraction);
    }
    remaining-=lengths[i];
  }
  return points[0];
}
function updateRouteTimeBubble(){
  const route=currentRoutes[selectedRoute],point=routeMidpoint(route?.geometry);
  if(!point){routeTimeBubble?.remove();routeTimeBubble=null;return;}
  const content=document.createElement('span');content.textContent=duration(route.travel_time_s);
  content.setAttribute('aria-label',`Tiempo estimado: ${content.textContent}`);
  if(!routeTimeBubble)routeTimeBubble=L.tooltip({
    permanent:true,direction:'top',offset:[0,-10],className:'route-time-bubble',interactive:false,opacity:1
  });
  routeTimeBubble.setLatLng(point).setContent(content);
  if(!map.hasLayer(routeTimeBubble))routeTimeBubble.addTo(map);
}
function routeStrokeStyle(active,casing,level){
  if(casing)return {color:'#fff',weight:active?13:10,opacity:active?1:.65};
  return {color:trafficEnabled?routeTrafficColors[level]:'#8552c4',
    weight:active?8:6,opacity:active?1:.55};
}
function selectRoute(index){
  selectedRoute=index;
  els.routeLegend.classList.toggle('hidden',!trafficEnabled||!currentRoutes.length);
  routeLayers.forEach((group,i)=>group.eachLayer(line=>{
    const active=i===index,casing=line.options.routeCasing;
    line.setStyle(routeStrokeStyle(active,casing,line.options.routeTrafficLevel));
  }));
  routeLayers[index]?.eachLayer(line=>line.bringToFront());
  els.routesList.querySelectorAll('.route-card').forEach((card,i)=>{
    card.classList.toggle('active',i===index);card.setAttribute('aria-pressed',String(i===index));
  });
  updateRouteTimeBubble();
}
function routePieces(route){
  if(!route.segments?.length)return [{geometry:route.geometry,level:0}];
  const pieces=[];
  for(const segment of route.segments){
    if(!segment.geometry||segment.geometry.length<2)continue;
    const level=Math.max(0,Math.min(2,Number(segment.traffic_level)||0));
    const previous=pieces[pieces.length-1],first=segment.geometry[0],last=previous?.geometry.at(-1);
    if(previous?.level===level&&last[0]===first[0]&&last[1]===first[1])previous.geometry.push(...segment.geometry.slice(1));
    else pieces.push({geometry:segment.geometry.map(p=>[...p]),level});
  }
  return pieces.length?pieces:[{geometry:route.geometry,level:0}];
}
function renderRoutes(data,{fitBounds=true,preferredRoute=0}={}){
  clearRoutes();currentRoutes=data.routes||[];
  els.routeCount.textContent=String(currentRoutes.length);els.routesList.replaceChildren();
  currentRoutes.forEach((route,index)=>{
    const group=L.featureGroup().addTo(map);
    const pathOptions={pane:'routePane',renderer:routeRenderer,lineCap:'round',lineJoin:'round',bubblingMouseEvents:false};
    const casing=L.polyline(route.geometry.map(([lon,lat])=>[lat,lon]),{
      ...pathOptions,color:'#fff',weight:12,opacity:1,routeCasing:true
    }).addTo(group);
    casing.on('click',()=>selectRoute(index));
    for(const piece of routePieces(route)){
      const line=L.polyline(piece.geometry.map(([lon,lat])=>[lat,lon]),{
        ...pathOptions,color:trafficEnabled?routeTrafficColors[piece.level]:routeSolidColors[index%routeSolidColors.length],weight:7,opacity:1,routeCasing:false,routeTrafficLevel:piece.level
      }).addTo(group);
      line.on('click',()=>selectRoute(index));
    }
    routeLayers.push(group);
    const card=document.createElement('button');card.type='button';card.className='route-card';
    card.innerHTML=`<span class="route-top"><span class="route-name">${escapeHtml(route.name)}</span><span class="route-time">${duration(route.travel_time_s)}</span></span><span class="route-bottom"><span>${route.distance_km.toFixed(1)} km</span><span class="route-tag">${escapeHtml(route.tag)}</span></span><span class="route-arrival">Llegada: ${escapeHtml(localFormat.format(new Date(Math.ceil(new Date(route.arrival_local).getTime()/60000)*60000)))}</span>`;
    card.addEventListener('click',()=>selectRoute(index));els.routesList.append(card);
  });
  if(currentRoutes.length){
    selectRoute(Math.min(preferredRoute,currentRoutes.length-1));
    if(fitBounds)map.fitBounds(L.featureGroup(routeLayers).getBounds(),{
      paddingTopLeft:[35,100],paddingBottomRight:[window.innerWidth>760?365:35,60],maxZoom:16
    });
  }
  status(`${currentRoutes.length} ${currentRoutes.length===1?'ruta eficiente':'rutas eficientes'}`);
}
async function calculateRoutes({automatic=false,signal}={}){
  if(signal?.aborted||!startPoint||!endPoint||!els.modelSelect.value||!departure())return 0;
  if(routeRequestActive){routeRecalcQueued=true;return 0;}
  clearTimeout(routeRecalcTimer);routeRecalcTimer=null;routeRecalcQueued=false;
  if(!automatic)autoRouteEnabled=true;
  const epoch=++routeEpoch,previousSelection=automatic?selectedRoute:0;
  routeController=new AbortController();routeRequestActive=busy=true;ready();
  const controller=routeController,abort=()=>controller.abort();
  signal?.addEventListener('abort',abort,{once:true});
  els.calculateBtn.textContent=automatic?'Actualizando…':'Calculando…';
  status(automatic?'Actualizando rutas…':'Calculando el tráfico durante el viaje…');
  try{
    const response=await fetch('/api/routes',{
      method:'POST',headers:{'Content-Type':'application/json'},signal:routeController.signal,
      body:JSON.stringify({start:[startPoint.lng,startPoint.lat],end:[endPoint.lng,endPoint.lat],departure:departure(),model:els.modelSelect.value})
    });
    const data=await response.json();
    if(epoch!==routeEpoch)return;
    if(!response.ok)throw new Error(data.error||'No se pudieron calcular las rutas.');
    renderRoutes(data,{fitBounds:!automatic,preferredRoute:previousSelection});
    return currentRoutes.length;
  }catch(error){
    if(error.name!=='AbortError'&&epoch===routeEpoch){
      clearRoutes();els.routesList.firstElementChild.textContent=error.message;status(error.message);
    }
    return 0;
  }finally{
    signal?.removeEventListener('abort',abort);
    routeRequestActive=busy=false;routeController=null;
    els.calculateBtn.textContent='Calcular rutas';ready();
    if(routeRecalcQueued){routeRecalcQueued=false;calculateRoutes({automatic:true});}
  }
}
els.calculateBtn.addEventListener('click',()=>calculateRoutes());
function cancelTrafficRequest(){
  trafficEpoch++;trafficController?.abort();trafficController=null;trafficRequestKey=null;
}
function clearTraffic(){
  clearTimeout(trafficTimer);trafficTimer=null;cancelTrafficRequest();trafficLoadedKey=null;
  trafficLayer?.remove();trafficLayer=null;els.trafficTime.textContent='Actualizando estimación…';map.closePopup();
}
function trafficLabel(value){const start=new Date(value);start.setUTCMinutes(0,0,0);const end=new Date(start.getTime()+3600000);return localFormat.format(start)+'–'+new Intl.DateTimeFormat('es-SV',{timeZone:'America/El_Salvador',hour:'2-digit',minute:'2-digit',hour12:true}).format(end);}
function trafficViewRequest(){
  if(!trafficEnabled||trafficMoving||!els.modelSelect.value||!departure())return null;
  const b=map.getBounds();
  const params=new URLSearchParams({model:els.modelSelect.value,at:departure(),zoom:String(map.getZoom()),format:'display',
    bbox:[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].map(value=>value.toFixed(6)).join(',')});
  const revision=models.find(model=>model.id===els.modelSelect.value)?.revision||'';
  return {params,key:`${revision}:${params}`};
}
function scheduleTraffic(){
  clearTimeout(trafficTimer);trafficTimer=null;
  const request=trafficViewRequest();
  if(!request)return;
  if(trafficViewCache.has(request.key))loadTraffic();
  else trafficTimer=setTimeout(()=>{trafficTimer=null;loadTraffic();},120);
}
function trafficDisplayData(data){
  const groups=new Map();
  for(const feature of data.features){
    const geometry=feature.geometry;
    if(!geometry||!['LineString','MultiLineString'].includes(geometry.type))continue;
    let level=Number(feature.properties?.level);
    if(!Number.isInteger(level)||level<0||level>3)level=3;
    const local=Boolean(feature.properties?.local),key=`${level}:${local}`;
    if(!groups.has(key))groups.set(key,{
      type:'Feature',properties:{level,local},geometry:{type:'MultiLineString',coordinates:[]}
    });
    const lines=geometry.type==='LineString'?[geometry.coordinates]:geometry.coordinates;
    for(const line of lines)if(line.length>=2)groups.get(key).geometry.coordinates.push(line);
  }
  return {type:'FeatureCollection',features:[...groups.values()].sort((a,b)=>a.properties.level-b.properties.level)};
}
function showTrafficView(view,key){
  const zoom=map.getZoom(),majorWeight=zoom<10?1.5:zoom<12?2:zoom<14?2.5:3;
  const next=L.geoJSON(view.display,{
    pane:'trafficPane',renderer:trafficRenderer,interactive:false,
    style:feature=>({color:['#35b57b','#e5b64b','#e26064','#8c96a2'][feature.properties.level],
      weight:feature.properties.local?1.5:majorWeight,opacity:.95})
  });
  next.addTo(map);trafficLayer?.remove();trafficLayer=next;trafficLoadedKey=key;
  if(routeLayers.length)selectRoute(selectedRoute);
  els.trafficTime.textContent=`${trafficLabel(view.at)} · ${view.model}`;
}
async function loadTraffic(){
  const request=trafficViewRequest();
  if(!request)return;
  const {params,key}=request;
  if(key===trafficRequestKey||(key===trafficLoadedKey&&trafficLayer))return;
  cancelTrafficRequest();
  const cached=trafficViewCache.get(key);
  if(cached){
    trafficViewCache.delete(key);trafficViewCache.set(key,cached);
    showTrafficView(cached,key);return;
  }
  const epoch=trafficEpoch,controller=new AbortController();
  trafficController=controller;trafficRequestKey=key;
  try{
    const response=await fetch(`/api/traffic?${params}`,{signal:controller.signal});
    if(epoch!==trafficEpoch||!trafficEnabled||trafficMoving)return;
    const data=await response.json();
    if(epoch!==trafficEpoch||!trafficEnabled||trafficMoving)return;
    if(!response.ok)throw new Error(data.error||'No se pudo mostrar el tráfico.');
    const view={display:trafficDisplayData(data),at:data.at,model:data.model};
    trafficViewCache.set(key,view);
    while(trafficViewCache.size>6)trafficViewCache.delete(trafficViewCache.keys().next().value);
    showTrafficView(view,key);
  }catch(error){
    if(error.name!=='AbortError'&&epoch===trafficEpoch)status(error.message);
  }finally{
    if(epoch===trafficEpoch){trafficController=null;trafficRequestKey=null;}
  }
}
function setTrafficMode(enabled){
  if(trafficEnabled===enabled)return;
  trafficEnabled=enabled;
  els.normalMode.setAttribute('aria-pressed',String(!enabled));
  els.trafficMode.setAttribute('aria-pressed',String(enabled));
  els.trafficLegend.classList.toggle('hidden',!enabled);
  if(routeLayers.length)selectRoute(selectedRoute);
  if(enabled)loadTraffic();else{clearTimeout(trafficTimer);clearTraffic();}
}
els.normalMode.addEventListener('click',()=>setTrafficMode(false));
els.trafficMode.addEventListener('click',()=>setTrafficMode(true));
map.on('movestart',()=>{
  trafficMoving=true;clearTimeout(trafficTimer);trafficTimer=null;cancelTrafficRequest();
});
map.on('moveend',()=>{trafficMoving=false;scheduleTraffic();});

const PLACE_SEARCH_URL='/search';
const placeSearchCache=new Map();
function removeSearchMarker(){dismissMapActions();const marker=searchMarker;searchMarker=null;marker?.remove();}
function promotePlace(kind,marker,name){
  const point=marker.getLatLng();
  removeSearchMarker();
  setPoint(kind,point,name);
}
function showPlace(item){
  clearSelection();removeSearchMarker();
  const marker=L.marker([item.lat,item.lon],{
    icon:L.divIcon({className:'place-marker',html:'<span></span>',iconSize:[24,32],iconAnchor:[12,32],popupAnchor:[0,-32]}),title:item.name,bubblingMouseEvents:false
  }).addTo(map);
  searchMarker=marker;
  const name=document.createElement('strong');name.textContent=item.name;
  marker.bindPopup(name,{
    className:'place-name-popup',autoPan:false,closeOnClick:true,autoClose:false,maxWidth:220
  }).openPopup();
  function openPlaceActions(){
    marker.openPopup();
    const point=map.latLngToContainerPoint(marker.getLatLng()),size=map.getSize();
    const rightEdge=window.innerWidth>760?document.querySelector('.panel').getBoundingClientRect().left:size.x;
    const offset=point.x+340<rightEdge?[220,70]:point.x>340?[-220,70]:[
      Math.max(115,Math.min(rightEdge-115,point.x))-point.x,
      Math.min(size.y-20,point.y+245)-point.y
    ];
    showMapActions(marker.getLatLng(),'Acciones',[
      {label:'Eliminar',danger:true,run:removeSearchMarker},
      {label:'Marcar como Inicio',run:()=>promotePlace('start',marker,item.name)},
      {label:'Marcar como Destino',run:()=>promotePlace('end',marker,item.name)}
    ],false,{className:'map-action-popup place-options-popup',offset});
  }
  marker.on('click',openPlaceActions);
  marker.on('contextmenu',event=>{
    if(event.originalEvent)L.DomEvent.stop(event.originalEvent);
    openPlaceActions();
  });
  map.flyTo([item.lat,item.lon],16);
  els.searchInput.value=item.name;els.clearSearch.disabled=false;els.searchSubmit.disabled=false;
  els.searchResults.classList.add('hidden');
}
function searchMessage(text){
  els.searchResults.replaceChildren();
  const message=document.createElement('div');message.className='search-message';message.textContent=text;
  els.searchResults.append(message);els.searchResults.classList.remove('hidden');
}
async function searchPlace(){
  const query=els.searchInput.value.trim(),epoch=++searchEpoch;
  searchController?.abort();
  if(query.length<2){els.searchResults.classList.add('hidden');return;}
  const controller=new AbortController();searchController=controller;
  const timeout=setTimeout(()=>controller.abort(),12000);
  searchMessage('Buscando lugares…');
  try{
    const key=query.toLocaleLowerCase('es');
    let results=placeSearchCache.get(key);
    if(!results){
      const params=new URLSearchParams({q:query});
      const response=await fetch(`${PLACE_SEARCH_URL}?${params}`,{signal:controller.signal});
      if(!response.ok)throw new Error('No se pudo consultar la base local de lugares.');
      const data=await response.json();
      results=data.filter(item=>item.name&&Number.isFinite(item.lat)&&Number.isFinite(item.lon));
      if(placeSearchCache.size>=30)placeSearchCache.delete(placeSearchCache.keys().next().value);
      placeSearchCache.set(key,results);
    }
    if(epoch!==searchEpoch)return;
    els.searchResults.replaceChildren();
    if(!results.length){searchMessage('No se encontraron lugares. Prueba con otro nombre.');return;}
    for(const item of results){
      const row=document.createElement('button');row.type='button';row.className='search-result';
      row.innerHTML=`<span class="search-result-icon"><img src="/frontend/assets/search.svg" alt=""></span><span><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.label)}</span></span>`;
      row.addEventListener('click',()=>{searchEpoch++;searchController?.abort();showPlace(item);});
      els.searchResults.append(row);
    }
  }catch(error){if(epoch===searchEpoch)searchMessage('No se pudo consultar la base local de lugares. Revisa la instalación de Navia.');}
  finally{clearTimeout(timeout);}
}
els.searchInput.addEventListener('input',()=>{
  searchEpoch++;searchController?.abort();els.clearSearch.disabled=!els.searchInput.value;
  clearTimeout(searchTimer);els.searchResults.classList.add('hidden');
  els.searchSubmit.disabled=els.searchInput.value.trim().length<2;
  if(!els.searchSubmit.disabled)searchTimer=setTimeout(searchPlace,300);
});
els.searchSubmit.addEventListener('click',searchPlace);
els.searchInput.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();clearTimeout(searchTimer);searchPlace();}});
els.clearSearch.addEventListener('click',()=>{
  searchEpoch++;searchController?.abort();clearTimeout(searchTimer);removeSearchMarker();
  els.searchInput.value='';els.clearSearch.disabled=true;els.searchSubmit.disabled=true;els.searchResults.replaceChildren();els.searchResults.classList.add('hidden');els.searchInput.focus();
});
els.resetBtn.addEventListener('click',()=>{
  autoRouteEnabled=false;
  dismissMapActions();
  startMarker?.remove();endMarker?.remove();startPoint=endPoint=startMarker=endMarker=null;
  els.startInput.value=els.endInput.value='';invalidateRoutes();clearSelection();status('');
});
document.addEventListener('click',event=>{if(!event.target.closest('.search-shell'))els.searchResults.classList.add('hidden');});
renderDeparture();loadModels();

// Exhibition tour uses the same search markers, routing endpoint, alternatives
// and time controls as manual planning; it never substitutes saved routes.
window.naviaDemo={
  async models(){if(!models.length)await loadModels();return models.map(({id,name})=>({id,name}));},
  model(id){
    if(els.modelSelect.value===id)return;
    els.modelSelect.value=id;invalidateRoutes();clearTraffic();
    els.modelIcon.src=models.find(model=>model.id===id)?.icon||'/frontend/assets/Verdict.svg';
    scheduleTraffic();
  },
  running(value){demoRunning=value;ready();},
  begin(){
    autoRouteEnabled=false;clearTimeout(routeRecalcTimer);routeRecalcQueued=false;
    removeSearchMarker();startMarker?.remove();endMarker?.remove();
    startPoint=endPoint=startMarker=endMarker=null;els.startInput.value=els.endInput.value='';
    invalidateRoutes();clearSelection();setTrafficMode(true);
    els.plannerBody.hidden=false;els.panelToggle.setAttribute('aria-expanded','true');
    els.panelToggle.setAttribute('aria-label','Contraer planificador');els.panelToggle.textContent='−';
    document.querySelector('.panel').scrollTo({top:0,behavior:'smooth'});
  },
  cancel(){
    routeEpoch++;routeController?.abort();clearTimeout(routeRecalcTimer);routeRecalcQueued=false;
    autoRouteEnabled=Boolean(currentRoutes.length);map.stop();
    document.querySelectorAll('.demo-focus').forEach(element=>element.classList.remove('demo-focus'));
  },
  guide(target){
    document.querySelectorAll('.demo-focus').forEach(element=>element.classList.remove('demo-focus'));
    const selectors={search:'.search-shell',place:'.map-action-popup',points:'.field-card',
      time:'.departure-card',calculate:'#calculateBtn',routes:'.routes-list',modes:'.view-switch',
      traffic:'#trafficLegend',zoom:'.leaflet-control-zoom',model:'.model-picker',reset:'#resetBtn',panel:'#panelToggle'};
    const element=target==='marker'?startMarker?.getElement():document.querySelector(selectors[target]||'#demoPanel');
    element?.classList.add('demo-focus');
    if(element?.closest('.panel'))element.scrollIntoView({block:'nearest',behavior:'smooth'});
  },
  async search(place,signal){
    searchEpoch++;searchController?.abort();clearTimeout(searchTimer);
    els.searchInput.value=place.name;els.clearSearch.disabled=false;els.searchSubmit.disabled=false;
    const abort=()=>{searchEpoch++;searchController?.abort();};
    signal.addEventListener('abort',abort,{once:true});
    try{await searchPlace();}finally{signal.removeEventListener('abort',abort);}
  },
  show(place){showPlace(place);},
  actions(){searchMarker?.fire('click');},
  pointActions(){startMarker?.fire('contextmenu');},
  removeStart(){removePoint('start');},
  reset(){els.resetBtn.click();},
  mode(enabled){setTrafficMode(enabled);},
  panel(expanded){
    els.plannerBody.hidden=!expanded;els.panelToggle.setAttribute('aria-expanded',String(expanded));
    els.panelToggle.setAttribute('aria-label',expanded?'Contraer planificador':'Abrir planificador');
    els.panelToggle.textContent=expanded?'−':'+';
  },
  pointsOverview(){
    if(startPoint&&endPoint)map.fitBounds(L.latLngBounds(startPoint,endPoint),{
      paddingTopLeft:[45,150],paddingBottomRight:[window.innerWidth>760?365:35,320],maxZoom:15
    });
  },
  focusStart(){if(startPoint)map.flyTo(startPoint,17,{duration:1});},
  async moveStart(coordinates,place,signal){
    if(!startMarker||!coordinates)return;
    const marker=startMarker,from=marker.getLatLng(),to=L.latLng(coordinates[1],coordinates[0]);
    marker.fire('dragstart');
    await new Promise((resolve,reject)=>{
      let frame;const began=performance.now();
      const abort=()=>{cancelAnimationFrame(frame);marker.setLatLng(from);reject(new DOMException('Tour detenido','AbortError'));};
      if(signal.aborted){abort();return;}
      signal.addEventListener('abort',abort,{once:true});
      const animate=now=>{
        const fraction=Math.min(1,(now-began)/1000);
        marker.setLatLng([from.lat+(to.lat-from.lat)*fraction,from.lng+(to.lng-from.lng)*fraction]);
        if(fraction<1){frame=requestAnimationFrame(animate);return;}
        signal.removeEventListener('abort',abort);marker.fire('dragend');
        els.startInput.value=`Acceso a ${place.name}`;resolve();
      };
      frame=requestAnimationFrame(animate);
    });
  },
  mark(kind,place){removeSearchMarker();setPoint(kind,[place.lat,place.lon],place.name);},
  time(value){
    autoRouteEnabled=false;setDeparture(value);departureChanged();
    els.departureHour.closest('.departure-card').scrollIntoView({block:'nearest',behavior:'smooth'});
  },
  today(){return localNow().slice(0,10);},
  async calculate(signal,comparison=false){
    autoRouteEnabled=false;
    return await calculateRoutes({automatic:comparison,signal});
  },
  alternative(index){
    if(!currentRoutes[index])return;
    selectRoute(index);
    els.routesList.querySelectorAll('.route-card')[index]?.scrollIntoView({block:'nearest',behavior:'smooth'});
  },
  overview(){
    if(!routeLayers.length)return;
    map.fitBounds(L.featureGroup(routeLayers).getBounds(),{
      paddingTopLeft:[45,150],paddingBottomRight:[window.innerWidth>760?365:35,
        Math.min(map.getSize().y*.55,(document.getElementById('demoPanel')?.offsetHeight||300)+40)],maxZoom:15
    });
  },
  focus(){
    const point=routeMidpoint(currentRoutes[selectedRoute]?.geometry);
    if(point)map.flyTo(point,Math.min(16,Math.max(14,map.getZoom()+1)),{duration:1.2});
  },
  summary(){return currentRoutes[selectedRoute]?duration(currentRoutes[selectedRoute].travel_time_s):'';}
};
