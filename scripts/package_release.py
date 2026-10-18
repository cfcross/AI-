"""Package clean source and skill, optionally with verified model weights."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {'app.py', 'asr_engine.py', 'market_language.py', 'config.example.json', 'setup.ps1', 'start.ps1',
       'start.cmd', 'stop.cmd', 'start-workbuddy.cmd', 'stop-workbuddy.cmd',
       'send-latest-workbuddy.cmd', 'requirements-runtime.txt', 'requirements-workbuddy.txt',
       'requirements-dev.txt', 'README.md', 'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md',
       '.gitignore', '.gitattributes'}

def source_files():
    paths = [ROOT / name for name in TOP if (ROOT / name).is_file()]
    for directory in ('sip', 'web', 'scripts', 'tests', 'skills', 'docs', 'runtime', 'workbuddy'):
        for path in (ROOT / directory).rglob('*'):
            if not path.is_file() or any(p in ('__pycache__', '.git', 'data', 'out') for p in path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() not in ('.py', '.mjs', '.js', '.json', '.md', '.txt', '.html', '.css', '.ps1', '.cmd', '.svg') and path.name != 'LICENSE':
                continue
            if path.name not in ('desktop-profile.json', '_user_meta.json'):
                paths.append(path)
    return sorted(set(paths))

def package(include_model=False):
    paths = source_files()
    if include_model:
        config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))
        model = (ROOT / config['asr']['model_path']).resolve()
        if not model.is_relative_to(ROOT / 'models'):
            raise ValueError('Model must be inside models/')
        manifest = model / 'download-manifest.json'
        for item in json.loads(manifest.read_text(encoding='utf-8'))['files']:
            path = (model / item['Path']).resolve()
            if not path.is_relative_to(model):
                raise ValueError('Unsafe model path')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['Sha256']:
                    raise ValueError('Model checksum mismatch: ' + item['Path'])
            paths.append(path)
        paths.append(manifest)
    output = ROOT / 'portable' / ('AIPrinter-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.zip')
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in paths:
            archive.write(path, path.relative_to(ROOT))
    print(output)
    return output

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--include-model', action='store_true')
    package(parser.parse_args().include_model)
