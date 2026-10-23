from pathlib import Path
from threading import Lock,RLock
import hashlib,json,math,unicodedata
import numpy as np
from flask import Flask,jsonify,request,send_from_directory
from rapidfuzz import fuzz
from .network import Network
from .registry import ModelRegistry
from .traffic import TrafficService,local_datetime
from .router import Router,NoRouteError

ROOT=Path(__file__).resolve().parents[1]

def normalized(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c)).casefold().split())

class Services:
    def __init__(self,root):self.root=Path(root);self.lock=RLock();self.route_lock=Lock();self.network=None;self.search_index=None
    def ready(self):
        with self.lock:
            if self.network is None:
                self.network=Network(self.root/'data/network');self.models=ModelRegistry(self.root/'models',self.network)
                self.traffic=TrafficService(self.network);self.router=Router(self.network,self.traffic)
        return self
    def search(self,query):
        with self.lock:
            if self.search_index is None:
                records=[];seen=set();n=self.network
                for i,name in enumerate(n.names):
                    if not name or not n.navigable[i]:continue
                    name_key=normalized(name);key=(name_key,round(float(n.lon[i]),2),round(float(n.lat[i]),2))
                    if key in seen:continue
                    seen.add(key);records.append((name_key,{'name':name,'label':'El Salvador','lon':float(n.lon[i]),'lat':float(n.lat[i])}))
                self.search_index=records
        key=normalized(query)
        ranked=sorted(((100. if key==name else 96. if key in name else float(fuzz.WRatio(key,name)),record) for name,record in self.search_index),key=lambda x:-x[0])
        return [{**record,'score':round(score,1)} for score,record in ranked[:8] if score>=55]

def create_app(root=ROOT):
    root=Path(root);app=Flask(__name__,static_folder=str(root/'frontend'),static_url_path='/frontend')
    app.config.update(MAX_CONTENT_LENGTH=16384,JSON_SORT_KEYS=False)
    services=Services(root);app.extensions['navia']=services

    @app.get('/')
    def index():return send_from_directory(root/'frontend','index.html')

    @app.get('/api/status')
    def status():
        s=services.ready()
        return jsonify(ready=True,execution='CPU',segments=len(s.network.segment_ids),timezone='America/El_Salvador')

    @app.get('/api/models')
    def models():
        entries=services.ready().models.discover()
        return jsonify(models=[{key:entry[key] for key in ('id','name','version','icon')}|
            {'revision':hashlib.sha256(repr(entry['signature']).encode()).hexdigest()[:16]} for entry in entries])

    @app.get('/search')
    def search():
        query=request.args.get('q','').strip()[:120]
        return jsonify(services.ready().search(query) if len(query)>=2 else [])

    @app.get('/api/traffic')
    def traffic():
        s=services.ready();model,_=s.models.get(request.args.get('model'))
        try:bbox=list(map(float,request.args.get('bbox','').split(',')));zoom=float(request.args.get('zoom','12'))
        except ValueError:raise ValueError('Límites del mapa inválidos.') from None
        if not math.isfinite(zoom) or not 0<=zoom<=20:raise ValueError('Zoom inválido.')
        at=local_datetime(request.args.get('at'))
        return jsonify(s.traffic.view(model,bbox,zoom,at))

    @app.post('/route')
    @app.post('/api/routes')
    def route():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict):raise ValueError('La solicitud de rutas no es válida.')
        start=payload.get('start');end=payload.get('end')
        if start is None or end is None:raise ValueError('Selecciona el inicio y el destino.')
        s=services.ready()
        if not s.route_lock.acquire(blocking=False):return jsonify(error='Ya hay un cálculo en curso. Espera un momento y vuelve a intentarlo.'),409
        try:
            model,signature=s.models.get(payload.get('model'))
            at=local_datetime(payload.get('departure'))
            result=s.router.routes(start,end,at,model,signature,3)
            return jsonify(result)
        finally:s.route_lock.release()

    @app.errorhandler(NoRouteError)
    def no_route(error):return jsonify(error=str(error),routes=[]),422
    @app.errorhandler(ValueError)
    def invalid(error):return jsonify(error=str(error)),400
    @app.errorhandler(FileNotFoundError)
    def missing(error):return jsonify(error='Faltan archivos locales de la red o del modelo. Revisa la instalación de Navia.'),503
    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        if request.path.startswith('/api/') or request.path=='/route':response.headers['Cache-Control']='no-store'
        return response
    return app
