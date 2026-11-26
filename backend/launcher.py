from __future__ import annotations

import argparse
from contextlib import closing
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import re
import platform
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
HOST = '127.0.0.1'
OFFLINE_VERSION = 1


class SetupError(Exception):
    pass


def environment_python(root):
    return root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def check_package(root):
    for name in ('requirements.txt', 'backend/server.py', 'frontend/index.html',
                 'frontend/app.js', 'frontend/styles.css',
                 'frontend/vendor/leaflet/leaflet.js', 'frontend/vendor/leaflet/leaflet.css',
                 'frontend/assets/Navia.svg', 'frontend/assets/Verdict.svg', 'frontend/assets/search.svg',
                 'frontend/vendor/offline-map/maplibre-gl.js', 'frontend/vendor/offline-map/maplibre-gl.css',
                 'frontend/vendor/offline-map/leaflet-maplibre-gl.js', 'frontend/vendor/offline-map/style.json',
                 'data/network/manifest.json', 'data/el_salvador.geojson',
                 'data/offline/map.mbtiles', 'data/offline/places.sqlite',
                 'data/offline/map-source.json', 'data/offline/places-source.json'):
        if not (root / name).is_file():
            raise SetupError(f'Falta {name}. Extraiga el paquete completo de Navia.')
    for name in ('countries', 'departments', 'department-labels'):
        relative = f'data/offline/context/{name}.geojson'
        if not (root / relative).is_file():
            raise SetupError(f'Falta {relative}. Extraiga el paquete completo de Navia.')
    folders = [root / 'data/network', root / 'models', root / 'data/offline']
    if (root / 'data/wheels').is_dir():
        folders.append(root / 'data/wheels')
    for folder in folders:
        if not folder.is_dir():
            raise SetupError(f'Falta la carpeta {folder.name}. Descargue el paquete completo.')
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix.lower() in ('.npy', '.npz', '.parquet', '.mbtiles', '.sqlite', '.whl'):
                with path.open('rb') as stream:
                    pointer = stream.read(100).startswith(b'version https://git-lfs.github.com/spec/v1')
                if pointer:
                    raise SetupError(
                        'El paquete contiene referencias de Git LFS, no los datos reales. '
                        'Descarga el ZIP completo de Releases o ejecuta git lfs pull si clonaste el repositorio.'
                    )


def prepare_environment(root, argv):
    python = environment_python(root)
    if not python.is_file():
        if (root / '.venv/pyvenv.cfg').exists():
            raise SetupError('El entorno .venv esta incompleto o pertenece a otro sistema. '
                             'Renombre esa carpeta y vuelve a ejecutar Navia para crear uno nuevo.')
        print('[1/3] Creando el entorno virtual .venv...', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')],
                       cwd=root, check=True)
    if os.path.normcase(str(Path(sys.prefix).resolve())) != os.path.normcase(str((root / '.venv').resolve())):
        return subprocess.call([str(python), '-B', '-m', 'backend.launcher', *argv], cwd=root)
    return None


def requirements_satisfied(requirements):
    count = 0
    for line in requirements.read_text(encoding='utf-8-sig').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)\s*==\s*([A-Za-z0-9_.+!-]+)', line)
        if match is None:
            return False
        count += 1
        try:
            if importlib.metadata.version(match[1]) != match[2]:
                return False
        except importlib.metadata.PackageNotFoundError:
            return False
    return count > 0


def run_dependency_install(root, source_args):
    if subprocess.run([sys.executable, '-m', 'pip', '--version'],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        subprocess.run([sys.executable, '-m', 'ensurepip', '--upgrade'], check=True)
    subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                    *source_args, '--only-binary=:all:', '--no-input', '-r', str(root / 'requirements.txt')],
                   cwd=root, check=True)
    subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'check'], cwd=root, check=True)


def install_requirements(root):
    requirements = root / 'requirements.txt'
    if requirements_satisfied(requirements):
        print('[2/3] Las dependencias ya estan instaladas.', flush=True)
        return
    wheelhouse = root / 'data' / 'wheels'
    manifest_path = wheelhouse / 'manifest.json'
    preparation = ('En un equipo con Internet y la misma version de Python y sistema operativo, '
                   'ejecute python tools/prepare_offline_wheels.py y copie data/wheels con Navia.')
    if not manifest_path.is_file():
        print('[2/3] Instalando las dependencias desde PyPI. Esta instalacion necesita Internet...', flush=True)
        try:
            run_dependency_install(root, ['--index-url', 'https://pypi.org/simple'])
        except subprocess.CalledProcessError as error:
            raise SetupError('No se pudieron instalar las dependencias. Comprueba la conexion a Internet '
                             'y vuelve a abrir Navia. Revisa tambien el error de instalacion mostrado arriba.') from error
        if not requirements_satisfied(requirements):
            raise SetupError('Las dependencias no completaron la instalacion. Revisa el error mostrado arriba.')
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        expected_target = {
            'python': f'{sys.version_info.major}.{sys.version_info.minor}',
            'implementation': platform.python_implementation(),
            'platform': sysconfig.get_platform(),
        }
        if (manifest.get('schema') != 'navia-offline-wheels-v1'
                or manifest.get('target') != expected_target
                or manifest.get('requirements_sha256') != file_sha256(requirements)):
            raise SetupError('El paquete local de dependencias no corresponde a este Python o a requirements.txt. ' + preparation)
        if not manifest['wheels']:
            raise ValueError('Empty wheel manifest')
        for wheel in manifest['wheels']:
            name = wheel['name']
            if Path(name).name != name or not name.endswith('.whl'):
                raise ValueError('Invalid wheel name')
            path = wheelhouse / name
            if not path.is_file() or path.stat().st_size != wheel['bytes'] or file_sha256(path) != wheel['sha256']:
                raise SetupError(f'El wheel local {name} esta ausente o incompleto. ' + preparation)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SetupError('El manifiesto de dependencias offline esta incompleto o no es valido. ' + preparation) from error
    print('[2/3] Instalando las dependencias desde los archivos locales...', flush=True)
    run_dependency_install(root, ['--no-index', '--find-links', str(wheelhouse)])
    if not requirements_satisfied(requirements):
        raise SetupError('Las dependencias locales no completaron la instalacion. ' + preparation)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_offline_data(root, app, deep=False):
    """Check actual offline files/endpoints, not just the presence of a manifest."""
    from .offline_search import PlacesIndex

    root = Path(root).resolve()
    PlacesIndex(root / 'data/offline/places.sqlite').check()
    for name in ('map-source.json', 'places-source.json'):
        try:
            source = json.loads((root / 'data/offline' / name).read_text(encoding='utf-8'))
            if not isinstance(source, dict) or not source:
                raise ValueError('Empty source manifest')
        except (OSError, ValueError) as error:
            raise SetupError(f'No se pudo validar la procedencia local: {name}.') from error
    map_path = root / 'data/offline/map.mbtiles'
    try:
        with closing(sqlite3.connect(map_path.as_uri() + '?mode=ro', uri=True)) as db:
            tile = db.execute('SELECT zoom_level,tile_column,tile_row,length(tile_data) FROM tiles LIMIT 1').fetchone()
            if not tile or not tile[3]:
                raise SetupError('El mapa offline no contiene teselas. Vuelva a preparar data/offline.')
            if deep and db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise SetupError('El archivo map.mbtiles esta incompleto o danado.')
    except sqlite3.Error as error:
        raise SetupError('No se pudo abrir el archivo local del mapa. Vuelva a preparar data/offline.') from error

    client = app.test_client()
    for endpoint in ('/api/offline-map/metadata', '/api/offline-map/style.json'):
        response = client.get(endpoint)
        if response.status_code != 200 or not isinstance(response.json, dict):
            raise SetupError(f'El componente offline no esta disponible: {endpoint}.')
    style_response = client.get('/api/offline-map/style.json')
    style = style_response.json
    origin = urlsplit(style_response.request.url)
    asset_urls = [style[key] for key in ('glyphs', 'sprite') if isinstance(style.get(key), str)]
    for source in style.get('sources', {}).values():
        if isinstance(source.get('url'), str):
            asset_urls.append(source['url'])
        if isinstance(source.get('data'), str):
            asset_urls.append(source['data'])
        asset_urls.extend(source.get('tiles', []))
    if any((urlsplit(url).scheme or urlsplit(url).netloc)
           and (urlsplit(url).scheme, urlsplit(url).netloc) != (origin.scheme, origin.netloc)
           for url in asset_urls):
        raise SetupError('El estilo del mapa contiene direcciones externas; falta preparar sus recursos locales.')
    if deep:
        for name in ('countries', 'departments', 'department-labels'):
            response = client.get(f'/api/offline-map/context/{name}.geojson')
            if response.status_code != 200 or not response.json.get('features'):
                raise SetupError(f'Falta contexto local del mapa: {name}.')
            response.close()
        results = client.get('/search?q=San%20Salvador')
        if results.status_code != 200 or not isinstance(results.json, list) or not results.json:
            raise SetupError('El buscador local no encontro San Salvador. Revise places.sqlite.')
        for source in style.get('sources', {}).values():
            for template in source.get('tiles', []):
                z, x, tms_y, _ = tile
                url = template.replace('{z}', str(z)).replace('{x}', str(x)).replace('{y}', str((1 << z) - 1 - tms_y))
                response = client.get(url)
                if response.status_code != 200 or not response.data:
                    raise SetupError('No se pudo leer una tesela local del mapa.')
        fonts = {','.join(layer.get('layout', {}).get('text-font', []))
                 for layer in style.get('layers', []) if layer.get('layout', {}).get('text-font')}
        for font in fonts:
            for glyph_range in ('0-255', '256-511'):
                url = style['glyphs'].replace('{fontstack}', quote(font, safe='')).replace('{range}', glyph_range)
                response = client.get(url)
                if response.status_code != 200 or len(response.data) <= 2:
                    raise SetupError(f'Faltan los caracteres locales del mapa: {font}, {glyph_range}.')


def is_running_navia(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://{HOST}:{port}/api/status', timeout=2) as response:
            result = json.loads(response.read(16384))
        return (isinstance(result, dict) and result.get('ready') is True
                and result.get('execution') == 'CPU'
                and result.get('timezone') == 'America/El_Salvador'
                and result.get('offline') is True
                and result.get('offline_version') == OFFLINE_VERSION)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def choose_port(preferred):
    for port in range(preferred, min(preferred + 20, 65536)):
        with socket.socket() as probe:
            try:
                probe.bind((HOST, port))
            except OSError:
                if is_running_navia(port):
                    return port, True
            else:
                return port, False
    raise SetupError('No se encontro un puerto local disponible para Navia.')


def open_page(url):
    try:
        if not webbrowser.open(url, new=2):
            print(f'Abra esta direccion en tu navegador: {url}', flush=True)
    except (OSError, webbrowser.Error):
        print(f'Abra esta direccion en tu navegador: {url}', flush=True)


def open_when_ready(url, stop, timeout=45):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2) as response:
                ready = response.status == 200
            if ready:
                if not stop.is_set():
                    open_page(url)
                return
        except (OSError, urllib.error.URLError):
            pass
        stop.wait(.25)
    if not stop.is_set():
        print(f'El navegador no se abrio automaticamente. Puede abrir {url}', flush=True)


def run_server(root, preferred, browser=True):
    from .server import create_app
    from werkzeug.serving import make_server

    port, running = choose_port(preferred)
    url = f'http://{HOST}:{port}'
    if running:
        print(f'Navia ya estaba ejecutandose: {url}', flush=True)
        if browser:
            open_page(url)
        return 0
    print('[3/3] Cargando la red vial y el modelo...', flush=True)
    app = create_app(root)
    check_offline_data(root, app)
    services = app.extensions['navia'].ready()
    services.models.get()
    server = make_server(HOST, port, app, threaded=True)
    stop = threading.Event()
    print(f'Navia esta disponible en {url}', flush=True)
    print('Mantenga esta ventana abierta. Para detenerlo se debe pulsar Ctrl + C.', flush=True)
    if browser:
        threading.Thread(target=open_when_ready, args=(url, stop), daemon=True).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description='Verificando componentes')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--check', action='store_true', help='Comprobar la instalacion sin abrir el servidor.')
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 12):
            raise SetupError('Use Python 3.12 o posterior para preparar Navia.')
        if not 1024 <= args.port <= 65535:
            raise SetupError('El puerto debe estar entre 1024 y 65535.')
        for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
            os.environ.setdefault(key, '1')
        print('Navia - Verificando componentes', flush=True)
        check_package(ROOT)
        child = prepare_environment(ROOT, argv)
        if child is not None:
            return child
        install_requirements(ROOT)
        if args.check:
            from .server import create_app
            app = create_app(ROOT)
            check_offline_data(ROOT, app, deep=True)
            services = app.extensions['navia'].ready()
            services.models.get()
            print('Comprobacion completada: entorno, dependencias, mapa, lugares, red y modelo disponibles sin Internet.', flush=True)
            return 0
        return run_server(ROOT, args.port, not args.no_browser)
    except KeyboardInterrupt:
        print('\nNavia se detuvo.', flush=True)
        return 0
    except subprocess.CalledProcessError as error:
        print(f'\nNo se pudo completar la preparacion (codigo {error.returncode}). '
              'Revise el error mostrado arriba, los archivos locales y el espacio disponible.', flush=True)
        return 1
    except (SetupError, OSError, ValueError, ImportError, sqlite3.Error) as error:
        print(f'\nNo se pudo iniciar Navia: {error}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
