"use strict";
const map=L.map('map',{zoomControl:false,preferCanvas:true}).setView([13.6929,-89.2182],12);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
L.control.zoom({position:'bottomright'}).addTo(map);
const ids=['searchInput','searchResults','clearSearch','startInput','endInput','pickStart','pickEnd','selectionHint','departureInput','departureLabel','departureHour','departureMinute','departurePeriod','calculateBtn','routesList','routeCount','resetBtn','statusPill','statusText','modelSelect','modelIcon','normalMode','trafficMode','trafficLegend','trafficTime','trafficDetail','panelToggle','plannerBody'];
const els=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)]));
const localFormat=new Intl.DateTimeFormat('es-SV',{timeZone:'America/El_Salvador',weekday:'long',hour:'2-digit',minute:'2-digit',hour12:true});
let activeSelection=null,startPoint=null,endPoint=null,startMarker=null,endMarker=null,routeLayers=[],currentRoutes=[];
let selectedRoute=0,busy=false,models=[],modelFingerprint='',trafficEnabled=false,trafficLayer=null;
let routeController=null,routeEpoch=0,trafficController=null,trafficEpoch=0,searchController=null,searchEpoch=0,searchTimer=null,trafficTimer=null;
function status(text){els.statusText.textContent=text||'';els.statusPill.classList.toggle('hidden',!text);}
function escapeHtml(value){return String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');}
function departure(){
  if(![els.departureInput,els.departureHour,els.departureMinute].every(input=>input.validity.valid))return null;
  const hour=Number(els.departureHour.value)%12+(els.departurePeriod.textContent==='PM'?12:0);
  return `${els.departureInput.value}T${String(hour).padStart(2,'0')}:${els.departureMinute.value.padStart(2,'0')}:00-06:00`;
}
function localNow(){const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/El_Salvador',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;}
function setDeparture(value){
  const [date,time]=value.split('T'),[hour,minute]=time.split(':').map(Number);
  els.departureInput.value=date;
  els.departureHour.value=String(hour%12||12).padStart(2,'0');
  els.departureMinute.value=String(minute).padStart(2,'0');
  els.departurePeriod.textContent=hour>=12?'PM':'AM';
}
setDeparture(localNow());
function renderDeparture(){
  const date=els.departureInput;
  els.departureLabel.textContent=date.validity.valid?new Intl.DateTimeFormat('es-SV',{timeZone:'America/El_Salvador',weekday:'long'}).format(new Date(`${date.value}T12:00:00-06:00`)):'Elige fecha';
  els.departurePeriod.setAttribute('aria-label',`Cambiar a ${els.departurePeriod.textContent==='AM'?'PM':'AM'}`);
  for(const input of [els.departureHour,els.departureMinute]){
    if(input.validity.valid)input.setAttribute('aria-valuenow',String(Number(input.value)));
    else input.removeAttribute('aria-valuenow');
    input.setAttribute('aria-invalid',String(!input.validity.valid));
  }
}
function ready(){els.calculateBtn.disabled=busy||!startPoint||!endPoint||!els.modelSelect.value||!departure();}
function clearRoutes(){routeLayers.forEach(line=>map.removeLayer(line));routeLayers=[];currentRoutes=[];els.routeCount.textContent='0';els.routesList.replaceChildren();const div=document.createElement('div');div.className='empty-route';div.textContent='Selecciona inicio y destino para calcular rutas.';els.routesList.append(div);}
function invalidateRoutes(){routeEpoch++;routeController?.abort();routeController=null;busy=false;els.calculateBtn.textContent='Calcular rutas';clearRoutes();ready();}
function clearSelection(){activeSelection=null;for(const [kind,el] of [['start',els.pickStart],['end',els.pickEnd]]){el.classList.remove('active');el.setAttribute('aria-pressed','false');}els.selectionHint.textContent=startPoint&&endPoint?'Inicio y destino listos.':'Elige inicio y destino en el mapa.';map.getContainer().classList.remove('picking');}
function choose(kind){activeSelection=kind;els.pickStart.classList.toggle('active',kind==='start');els.pickEnd.classList.toggle('active',kind==='end');els.pickStart.setAttribute('aria-pressed',String(kind==='start'));els.pickEnd.setAttribute('aria-pressed',String(kind==='end'));els.selectionHint.textContent=kind==='start'?'Haz clic en el mapa para elegir el inicio.':'Haz clic en el mapa para elegir el destino.';map.getContainer().classList.add('picking');}
function icon(kind){return L.divIcon({className:'',html:`<div class="custom-marker ${kind}"><span>${kind==='start'?'I':'F'}</span></div>`,iconSize:[28,28],iconAnchor:[8,26]});}
function setPoint(kind,point,label){const p=L.latLng(point);invalidateRoutes();if(kind==='start'){startPoint=p;startMarker?.remove();startMarker=L.marker(p,{icon:icon(kind),draggable:true}).addTo(map);els.startInput.value=label||`${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`;startMarker.on('dragend',()=>setPoint('start',startMarker.getLatLng()));}else{endPoint=p;endMarker?.remove();endMarker=L.marker(p,{icon:icon(kind),draggable:true}).addTo(map);els.endInput.value=label||`${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`;endMarker.on('dragend',()=>setPoint('end',endMarker.getLatLng()));}clearSelection();ready();}
map.on('click',event=>{if(activeSelection)setPoint(activeSelection,event.latlng);});
els.pickStart.addEventListener('click',()=>choose('start'));els.pickEnd.addEventListener('click',()=>choose('end'));
els.resetBtn.addEventListener('click',()=>{startMarker?.remove();endMarker?.remove();startPoint=endPoint=startMarker=endMarker=null;els.startInput.value=els.endInput.value='';invalidateRoutes();clearSelection();status('');});
els.panelToggle.addEventListener('click',()=>{const expanded=els.panelToggle.getAttribute('aria-expanded')==='true';els.panelToggle.setAttribute('aria-expanded',String(!expanded));els.panelToggle.setAttribute('aria-label',expanded?'Abrir planificador':'Contraer planificador');els.panelToggle.textContent=expanded?'+':'−';els.plannerBody.hidden=expanded;});
async function loadModels(){try{const response=await fetch('/api/models');const data=await response.json();if(!response.ok)throw new Error(data.error||'No se pudieron detectar los modelos.');const fingerprint=JSON.stringify(data.models);if(fingerprint===modelFingerprint)return;const previous=els.modelSelect.value;const previousRevision=models.find(m=>m.id===previous)?.revision;models=data.models;els.modelSelect.replaceChildren();for(const model of models){const option=document.createElement('option');option.value=model.id;option.textContent=model.name;els.modelSelect.append(option);}if(models.some(m=>m.id===previous)){els.modelSelect.value=previous;if(previousRevision!==models.find(m=>m.id===previous)?.revision){invalidateRoutes();clearTraffic();}}else{let remembered;try{remembered=localStorage.getItem('navia-model');}catch{}if(models.some(m=>m.id===remembered))els.modelSelect.value=remembered;invalidateRoutes();}modelFingerprint=fingerprint;if(!models.length){const option=document.createElement('option');option.value='';option.textContent='Sin modelos instalados';els.modelSelect.append(option);status('Añade un modelo compatible a la carpeta models.');}else{els.modelIcon.src=models.find(m=>m.id===els.modelSelect.value)?.icon||'/frontend/assets/Verdict.svg';scheduleTraffic();}ready();}catch(error){status(error.message);}}
els.modelSelect.addEventListener('change',()=>{try{localStorage.setItem('navia-model',els.modelSelect.value);}catch{}invalidateRoutes();clearTraffic();status('');scheduleTraffic();});
window.addEventListener('focus',loadModels);setInterval(()=>{if(!document.hidden)loadModels();},15000);
function departureChanged(){renderDeparture();invalidateRoutes();clearTraffic();scheduleTraffic();status('');}
function stepDeparture(minutes=0,days=0){
  const value=departure();
  if(!value){[els.departureInput,els.departureHour,els.departureMinute].find(input=>!input.validity.valid)?.reportValidity();return;}
  // Shift a local wall clock without depending on the browser's time zone.
  const shifted=new Date(new Date(value.slice(0,19)+'Z').getTime()+(minutes+days*1440)*60000);
  const next=shifted.toISOString().slice(0,16);
  if(next<'2020-01-01T00:00'||next>'2100-12-31T23:59')return;
  setDeparture(next);departureChanged();
}
els.departureInput.addEventListener('change',departureChanged);
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
function selectRoute(index){selectedRoute=index;routeLayers.forEach((line,i)=>{line.setStyle({weight:i===index?7:5,opacity:i===index?.95:.35});if(i===index)line.bringToFront();});els.routesList.querySelectorAll('.route-card').forEach((card,i)=>{card.classList.toggle('active',i===index);card.setAttribute('aria-pressed',String(i===index));});}
function renderRoutes(data){clearRoutes();currentRoutes=data.routes||[];els.routeCount.textContent=String(currentRoutes.length);els.routesList.replaceChildren();const colors=['#739aff','#b59ad6','#87afb1'];currentRoutes.forEach((route,index)=>{const line=L.polyline(route.geometry.map(([lon,lat])=>[lat,lon]),{color:colors[index],weight:index===0?7:5,opacity:index===0?.95:.35,lineCap:'round',lineJoin:'round'}).addTo(map);line.on('click',()=>selectRoute(index));routeLayers.push(line);const card=document.createElement('button');card.type='button';card.className=`route-card${index===0?' active':''}`;card.setAttribute('aria-pressed',String(index===0));card.innerHTML=`<span class="route-top"><span class="route-name">${escapeHtml(route.name)}</span><span class="route-time">${duration(route.travel_time_s)}</span></span><span class="route-bottom"><span>${route.distance_km.toFixed(1)} km</span><span class="route-tag">${escapeHtml(route.tag)}</span></span><span class="route-arrival">Llegada: ${escapeHtml(localFormat.format(new Date(Math.ceil(new Date(route.arrival_local).getTime()/60000)*60000)))}</span><span class="route-conditions">${route.traffic_km.jam.toFixed(1)} km con atasco · Demora prevista: ${route.delay_minutes.toFixed(1)} min</span>`;card.addEventListener('click',()=>selectRoute(index));els.routesList.append(card);});if(currentRoutes.length){selectRoute(0);map.fitBounds(L.featureGroup(routeLayers).getBounds(),{paddingTopLeft:[35,100],paddingBottomRight:[window.innerWidth>760?365:35,60],maxZoom:16});}status(data.message||'Rutas calculadas.');}
els.calculateBtn.addEventListener('click',async()=>{if(!startPoint||!endPoint||busy)return;const epoch=++routeEpoch;routeController?.abort();routeController=new AbortController();busy=true;ready();els.calculateBtn.textContent='Calculando…';status('Calculando el tráfico durante el viaje…');try{const response=await fetch('/api/routes',{method:'POST',headers:{'Content-Type':'application/json'},signal:routeController.signal,body:JSON.stringify({start:[startPoint.lng,startPoint.lat],end:[endPoint.lng,endPoint.lat],departure:departure(),model:els.modelSelect.value})});const data=await response.json();if(epoch!==routeEpoch)return;if(!response.ok)throw new Error(data.error||'No se pudieron calcular las rutas.');renderRoutes(data);}catch(error){if(error.name==='AbortError'||epoch!==routeEpoch)return;clearRoutes();els.routesList.firstElementChild.textContent=error.message;status(error.message);}finally{if(epoch===routeEpoch){busy=false;els.calculateBtn.textContent='Calcular rutas';ready();}}});
function clearTraffic(){trafficEpoch++;trafficController?.abort();trafficLayer?.remove();trafficLayer=null;els.trafficTime.textContent='Actualizando estimación…';map.closePopup();}
function trafficLabel(value){const start=new Date(value);start.setUTCMinutes(0,0,0);const end=new Date(start.getTime()+3600000);return localFormat.format(start)+'–'+new Intl.DateTimeFormat('es-SV',{timeZone:'America/El_Salvador',hour:'2-digit',minute:'2-digit',hour12:true}).format(end);}
function scheduleTraffic(){clearTimeout(trafficTimer);trafficTimer=setTimeout(loadTraffic,250);}
async function loadTraffic(){const epoch=++trafficEpoch;trafficController?.abort();if(!trafficEnabled||!els.modelSelect.value||!departure())return;trafficController=new AbortController();const b=map.getBounds();const params=new URLSearchParams({model:els.modelSelect.value,at:departure(),zoom:String(map.getZoom()),bbox:[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].join(',')});els.trafficTime.textContent=trafficLabel(departure());try{const response=await fetch(`/api/traffic?${params}`,{signal:trafficController.signal});const data=await response.json();if(epoch!==trafficEpoch||!trafficEnabled)return;if(!response.ok)throw new Error(data.error||'No se pudo mostrar el tráfico.');const renderer=L.canvas({padding:.3});const next=L.geoJSON(data,{renderer,style:feature=>({color:['#35b57b','#e5b64b','#e26064','#8c96a2'][feature.properties.level],weight:feature.properties.local?1.5:3,opacity:.95}),onEachFeature:(feature,layer)=>{const p=feature.properties;layer.on('click',event=>{if(activeSelection){setPoint(activeSelection,event.latlng);return;}L.popup().setLatLng(event.latlng).setContent(`<strong>${escapeHtml(p.name)}</strong><br>${['Fluido','Moderado','Atasco','Cerrado'][p.level]} · ≈${p.speed_kmh} km/h<br>${escapeHtml(data.model)} · ${escapeHtml(localFormat.format(new Date(data.at)))}`).openOn(map);});}});trafficLayer?.remove();trafficLayer=next.addTo(map);routeLayers.forEach(line=>line.bringToFront());els.trafficTime.textContent=`${trafficLabel(data.at)} · ${data.model}`;els.trafficDetail.textContent=(data.locals_visible?'Incluye calles locales.':'Las calles locales aparecen al acercarse (zoom 16).')+(data.truncated?' Acércate para ver más detalle.':'');}catch(error){if(error.name!=='AbortError'&&epoch===trafficEpoch){trafficLayer?.remove();trafficLayer=null;els.trafficDetail.textContent=error.message;}}}
function setTrafficMode(enabled){
  if(trafficEnabled===enabled)return;
  trafficEnabled=enabled;
  els.normalMode.setAttribute('aria-pressed',String(!enabled));
  els.trafficMode.setAttribute('aria-pressed',String(enabled));
  els.trafficLegend.classList.toggle('hidden',!enabled);
  if(enabled)loadTraffic();else{clearTimeout(trafficTimer);clearTraffic();}
}
els.normalMode.addEventListener('click',()=>setTrafficMode(false));
els.trafficMode.addEventListener('click',()=>setTrafficMode(true));
map.on('zoomstart',()=>{trafficLayer?.remove();trafficLayer=null;});
map.on('moveend',scheduleTraffic);
async function searchPlace(){const query=els.searchInput.value.trim(),epoch=++searchEpoch;searchController?.abort();if(query.length<2){els.searchResults.classList.add('hidden');return;}searchController=new AbortController();try{const response=await fetch(`/search?q=${encodeURIComponent(query)}`,{signal:searchController.signal});const results=await response.json();if(epoch!==searchEpoch)return;if(!response.ok)throw new Error(results.error||'No se pudo buscar.');els.searchResults.replaceChildren();els.searchResults.classList.remove('hidden');if(!results.length){const empty=document.createElement('div');empty.className='search-result';empty.textContent='Sin resultados en la red vial local.';els.searchResults.append(empty);}for(const item of results){const row=document.createElement('button');row.type='button';row.className='search-result';row.innerHTML=`<span class="search-result-icon">⌖</span><span><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.label)}</span></span>`;row.addEventListener('click',()=>{map.flyTo([item.lat,item.lon],16);if(activeSelection)setPoint(activeSelection,[item.lat,item.lon],item.name);els.searchInput.value=item.name;els.searchResults.classList.add('hidden');});els.searchResults.append(row);}}catch(error){if(error.name!=='AbortError'&&epoch===searchEpoch){status(error.message);els.searchResults.classList.add('hidden');}}}
els.searchInput.addEventListener('input',()=>{els.clearSearch.disabled=!els.searchInput.value;clearTimeout(searchTimer);searchTimer=setTimeout(searchPlace,220);});
els.clearSearch.addEventListener('click',()=>{searchEpoch++;searchController?.abort();clearTimeout(searchTimer);els.searchInput.value='';els.clearSearch.disabled=true;els.searchResults.replaceChildren();els.searchResults.classList.add('hidden');els.searchInput.focus();});
document.addEventListener('click',event=>{if(!event.target.closest('.search-shell'))els.searchResults.classList.add('hidden');});
renderDeparture();loadModels();
