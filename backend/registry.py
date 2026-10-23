from pathlib import Path
from threading import RLock
import json
import numpy as np
from .verdict import ConditionalTrafficModel
from .verdict.hashing import sha256

class ModelRegistry:
    """Scan on demand; keep only the last requested model in CPU memory."""
    def __init__(self,folder,network):
        self.folder=Path(folder);self.network=network;self.lock=RLock();self.loaded=None;self.signature=None

    def discover(self):
        result=[]
        for directory in sorted(self.folder.iterdir(),key=lambda p:p.name.casefold()):
            if not directory.is_dir() or directory.is_symlink():continue
            try:
                manifest=json.loads((directory/'model_manifest.json').read_text(encoding='utf8'))
                normal=manifest.get('normal_model_path','base/traffic_model.npz')
                required={normal,'conditional_residual.npz','road_context.npz'}
                if not required<=manifest['artifact_sha256'].keys() or (directory/'INCOMPLETE').exists():continue
                for name in manifest['artifact_sha256']:
                    path=(directory/name).resolve()
                    if not path.is_relative_to(directory.resolve()) or not path.is_file():raise ValueError('Invalid model path')
                result.append({'id':directory.name,'name':str(manifest.get('name',directory.name))[:80],
                    'version':str(manifest.get('version','')),'icon':'/frontend/assets/Verdict.svg',
                    'signature':[(p.stat().st_mtime_ns,p.stat().st_size) for p in [directory/'model_manifest.json']+[directory/x for x in sorted(required)]]})
            except (OSError,KeyError,ValueError,TypeError):continue
        return result

    def get(self,model_id=None):
        with self.lock:
            entries=self.discover()
            if not entries:raise ValueError('No hay modelos compatibles instalados en la carpeta models.')
            model_id=model_id or entries[0]['id']
            entry=next((x for x in entries if x['id']==model_id),None)
            if entry is None:raise ValueError('El modelo seleccionado ya no está disponible.')
            signature=(model_id,repr(entry['signature']))
            if self.signature!=signature:
                try:model=ConditionalTrafficModel(self.folder/model_id)
                except (OSError,ValueError,EOFError) as error:
                    raise ValueError('El modelo está incompleto o sus archivos cambiaron. Termina de copiarlo; si clonaste el proyecto, ejecuta git lfs pull.') from error
                if not np.array_equal(model.ids,self.network.segment_ids) or not np.allclose(model.length,self.network.segment_length,rtol=1e-5,atol=.05):
                    raise ValueError('El modelo utiliza otra red vial; hay que preparar su red antes de seleccionarlo.')
                if not np.isfinite(model.free_speed).all() or not np.isfinite(model.free_time).all() or np.any(model.log_delay<0):
                    raise ValueError('El modelo contiene velocidades o demoras fuera del formato permitido.')
                if not np.allclose(model.free_speed*model.free_time,model.length*3.6,rtol=3e-5,atol=.001):
                    raise ValueError('Las unidades de velocidad, longitud y tiempo del modelo no son compatibles.')
                if model.thresholds.shape!=(2,) or not np.isfinite(model.thresholds).all() or not 1<=model.thresholds[0]<model.thresholds[1]:
                    raise ValueError('Los umbrales de tráfico del modelo no son válidos.')
                self.loaded=model;self.signature=signature
            return self.loaded,signature
