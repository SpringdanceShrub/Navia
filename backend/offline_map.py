from __future__ import annotations

from functools import lru_cache
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from urllib.parse import urljoin

from flask import Response, jsonify, request, send_from_directory


class OfflineMap:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'data/offline/map.mbtiles'
        self.assets = self.root / 'frontend/vendor/offline-map'

    def connect(self):
        # mode=ro also prevents accidentally creating an empty DB when data is missing.
        return sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)

    @lru_cache(maxsize=1)
    def metadata(self):
        if not self.path.is_file():
            raise FileNotFoundError('Falta el mapa local de El Salvador.')
        with closing(self.connect()) as db:
            data = dict(db.execute('SELECT name,value FROM metadata'))
        return {
            'ready': True, 'offline': True, 'schema': 'shortbread',
            'minzoom': int(data.get('minzoom', 0)),
            'maxzoom': int(data.get('maxzoom', 14)),
            'max_display_zoom': 19,
            'bounds': [-90.50091, 12.03598, -87.58438, 14.45762],
            'attribution': '© OpenStreetMap contributors · BBBike',
            'license': data.get('license', 'Open Database License 1.0'),
            'tiles': '/api/offline-map/tiles/{z}/{x}/{y}.pbf',
            'style': '/api/offline-map/style.json',
        }

    def style(self):
        data = json.loads((self.assets / 'style.json').read_text(encoding='utf-8'))
        info = self.metadata()
        source = data['sources']['offline']
        source['minzoom'] = info['minzoom']
        source['maxzoom'] = info['maxzoom']
        source['bounds'] = info['bounds']
        return data

    @lru_cache(maxsize=512)
    def tile(self, z, x, y):
        info = self.metadata()
        if not info['minzoom'] <= z <= info['maxzoom']:
            return None
        if not (0 <= x < 1 << z and 0 <= y < 1 << z):
            return None
        with closing(self.connect()) as db:
            row = db.execute(
                'SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?',
                (z, x, (1 << z) - 1 - y),
            ).fetchone()
        return bytes(row[0]) if row is not None else None


def register_offline_map(app, root):
    service = OfflineMap(root)
    app.extensions['navia_offline_map'] = service

    @app.get('/api/offline-map/metadata')
    def offline_map_metadata():
        return jsonify(service.metadata())

    @app.get('/api/offline-map/style.json')
    def offline_map_style():
        style = service.style()
        style['glyphs'] = urljoin(request.host_url, style['glyphs'])
        for source in style['sources'].values():
            if 'tiles' in source:
                source['tiles'] = [urljoin(request.host_url, url) for url in source['tiles']]
            if 'url' in source:
                source['url'] = urljoin(request.host_url, source['url'])
            if isinstance(source.get('data'), str):
                source['data'] = urljoin(request.host_url, source['data'])
        return jsonify(style)

    @app.get('/api/offline-map/context/<name>.geojson')
    def offline_map_context(name):
        if name not in {'countries', 'departments', 'department-labels'}:
            return Response(status=404)
        return send_from_directory(service.root / 'data/offline/context', name + '.geojson',
                                   mimetype='application/geo+json', max_age=86400)

    @app.get('/api/offline-map/tiles/<int:z>/<int:x>/<int:y>.pbf')
    def offline_map_tile(z, x, y):
        if z > 20:
            return Response(status=404)
        data = service.tile(z, x, y)
        if data is None:
            return Response(status=204)
        response = Response(data, content_type='application/vnd.mapbox-vector-tile')
        if data.startswith(b'\x1f\x8b'):
            response.headers['Content-Encoding'] = 'gzip'
        response.headers['Cache-Control'] = 'public, max-age=86400'
        return response

    return service
