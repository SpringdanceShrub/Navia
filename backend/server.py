from pathlib import Path
from threading import Lock,RLock
import hashlib,json,math
from flask import Flask,jsonify,request,send_from_directory
from .network import Network
from .registry import ModelRegistry
from .traffic import TrafficService,local_datetime
from .router import Router,NoRouteError
from .country import validate_route_point
from .offline_search import PlacesIndex
from .offline_map import register_offline_map

ROOT=Path(__file__).resolve().parents[1]

class Services:
    def __init__(self,root):
        self.root=Path(root);self.lock=RLock();self.route_lock=Lock();self.network=None
        self.places=PlacesIndex(self.root/'data/offline/places.sqlite')
    def ready(self):
        with self.lock:
            if self.network is None:
                self.network=Network(self.root/'data/network');self.models=ModelRegistry(self.root/'models',self.network)
                self.traffic=TrafficService(self.network);self.router=Router(self.network,self.traffic)
        return self
    def search(self,query):
        return self.places.search(query)

def create_app(root=ROOT):
    root=Path(root);app=Flask(__name__,static_folder=str(root/'frontend'),static_url_path='/frontend')
    app.config.update(MAX_CONTENT_LENGTH=16384,JSON_SORT_KEYS=False)
    services=Services(root);app.extensions['navia']=services
    register_offline_map(app,root)

    @app.get('/')
    def index():return send_from_directory(root/'frontend','index.html')

    @app.get('/api/status')
    def status():
        s=services.ready()
        return jsonify(ready=True,execution='CPU',segments=len(s.network.segment_ids),timezone='America/El_Salvador',offline=True,offline_version=1)

    @app.get('/api/models')
    def models():
        entries=services.ready().models.discover()
        return jsonify(models=[{key:entry[key] for key in ('id','name','version','icon')}|
            {'revision':hashlib.sha256(repr(entry['signature']).encode()).hexdigest()[:16]} for entry in entries])

    @app.get('/search')
    def search():
        query=request.args.get('q','').strip()[:120]
        return jsonify(services.search(query) if len(query)>=2 else [])

    @app.get('/api/traffic')
    def traffic():
        s=services.ready();model,_=s.models.get(request.args.get('model'))
        try:bbox=list(map(float,request.args.get('bbox','').split(',')));zoom=float(request.args.get('zoom','12'))
        except ValueError:raise ValueError('Límites del mapa inválidos.') from None
        if not math.isfinite(zoom) or not 0<=zoom<=20:raise ValueError('Zoom inválido.')
        at=local_datetime(request.args.get('at'))
        return jsonify(s.traffic.view(model,bbox,zoom,at,compact=request.args.get('format')=='display'))

    @app.post('/route')
    @app.post('/api/routes')
    def route():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict):raise ValueError('La solicitud de rutas no es válida.')
        start=payload.get('start');end=payload.get('end')
        if start is None or end is None:raise ValueError('Selecciona el inicio y el destino.')
        start=validate_route_point(start);end=validate_route_point(end)
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
        response.headers['Content-Security-Policy']=(
            "default-src 'self'; connect-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data:; worker-src 'self' blob:; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'")
        if (request.path.startswith('/api/') or request.path=='/route') and 'Cache-Control' not in response.headers:
            response.headers['Cache-Control']='no-store'
        return response
    return app
