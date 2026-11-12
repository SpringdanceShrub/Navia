from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
HOST = '127.0.0.1'


class SetupError(Exception):
    pass


def environment_python(root):
    return root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def check_package(root):
    for name in ('requirements.txt', 'backend/server.py', 'frontend/index.html',
                 'data/network/manifest.json'):
        if not (root / name).is_file():
            raise SetupError(f'Falta {name}. Extraiga el paquete completo de Navia.')
    for folder in (root / 'data/network', root / 'models'):
        if not folder.is_dir():
            raise SetupError(f'Falta la carpeta {folder.name}. Descargue el paquete completo.')
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix.lower() in ('.npy', '.npz', '.parquet'):
                with path.open('rb') as stream:
                    pointer = stream.read(100).startswith(b'version https://git-lfs.github.com/spec/v1')
                if pointer:
                    raise SetupError(
                        'El modelo o la red contienen referencias de Git LFS, no los datos reales. '
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


def install_requirements(root):
    requirements = root / 'requirements.txt'
    if requirements_satisfied(requirements):
        print('[2/3] Las dependencias ya estan instaladas.', flush=True)
        return
    print('[2/3] Instalando las dependencias de Navia. Se requiere una conexion a Internet...', flush=True)
    if subprocess.run([sys.executable, '-m', 'pip', '--version'],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        subprocess.run([sys.executable, '-m', 'ensurepip', '--upgrade'], check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--no-input', '-r', str(requirements)], cwd=root, check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=root, check=True)


def is_running_navia(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://{HOST}:{port}/api/status', timeout=2) as response:
            result = json.loads(response.read(16384))
        return (isinstance(result, dict) and result.get('ready') is True
                and result.get('execution') == 'CPU'
                and result.get('timezone') == 'America/El_Salvador')
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
            services = create_app(ROOT).extensions['navia'].ready()
            services.models.get()
            print('Comprobacion completada: entorno, dependencias, red y modelo disponibles.', flush=True)
            return 0
        return run_server(ROOT, args.port, not args.no_browser)
    except KeyboardInterrupt:
        print('\nNavia se detuvo.', flush=True)
        return 0
    except subprocess.CalledProcessError as error:
        print(f'\nNo se pudo completar la preparacion (codigo {error.returncode}). '
              'Revise el error mostrado arriba, la conexion a Internet y el espacio disponible.', flush=True)
        return 1
    except (SetupError, OSError, ValueError, ImportError) as error:
        print(f'\nNo se pudo iniciar Navia: {error}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
