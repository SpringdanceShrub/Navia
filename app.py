import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS'):
    os.environ.setdefault(name,'1')
from pathlib import Path
import argparse
from backend.server import create_app
app=create_app(Path(__file__).resolve().parent)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    app.run(host=args.host,port=args.port,debug=False,threaded=True)
