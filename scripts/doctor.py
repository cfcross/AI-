"""Read-only environment check. No model downloads or device changes."""
import importlib.metadata
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
config = json.loads((root / 'config.json').read_text(encoding='utf-8-sig'))
print('Python:', sys.version)
print('Executable:', sys.executable)
node = config['runtime']['node']
print('Node:', shutil.which(str(root / node) if '/' in node or '\\' in node else node))
for name in ('torch', 'qwen-asr', 'transformers', 'accelerate', 'nagisa'):
    try:
        print(name, importlib.metadata.version(name))
    except importlib.metadata.PackageNotFoundError:
        print(name, 'MISSING')
try:
    import torch
    print('Torch CUDA:', torch.version.cuda, '| CUDA available:', torch.cuda.is_available())
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        print('GPU:', p.name, '| VRAM GiB:', round(p.total_memory / 2**30, 2))
        # Check an actual CUDA allocation, beyond driver detection.
        print('CUDA allocation:', (torch.ones(1, device='cuda') + 1).item())
except Exception as error:
    print('GPU CHECK FAILED:', error)
model = root / config['asr']['model_path']
weights = (model / 'model.safetensors').is_file() or (model / 'model.safetensors.index.json').is_file()
print('Model:', model, '| config + weights present:', (model / 'config.json').is_file() and weights)
for name in ('sip_port', 'rtp_port'):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((config['phone']['local_address'], config['phone'][name]))
        print(name, 'AVAILABLE')
    except OSError as error:
        print(name, 'UNAVAILABLE:', error)
print('HT802:', config['phone']['device_address'], '| Off Hook Auto Dial must point to this PC.')
