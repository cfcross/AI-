"""Download official Qwen weights from ModelScope with resume and SHA256 verification."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request


def download(model, output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    api = f'https://modelscope.cn/api/v1/models/Qwen/Qwen3-ASR-{model}'
    manifest_path = output / 'download-manifest.json'
    if manifest_path.exists():
        files = json.loads(manifest_path.read_text(encoding='utf-8'))['files']
    else:
        with urllib.request.urlopen(api + '/repo/files?Revision=master&Recursive=true', timeout=45) as r:
            files = [f for f in json.load(r)['Data']['Files'] if f['Type'] == 'blob']
        manifest_path.write_text(json.dumps({'model': model, 'source': api, 'files': files}, ensure_ascii=False, indent=2), encoding='utf-8')
    for f in files:
        path = (output / f['Path']).resolve()
        if not path.is_relative_to(output):
            raise ValueError('Unsafe model path')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() == f['Sha256']:
                    print('Verified existing:', f['Path'], flush=True)
                    continue
        url = f'https://modelscope.cn/models/Qwen/Qwen3-ASR-{model}/resolve/' + f['Revision'] + '/' + urllib.parse.quote(f['Path'])
        print('Downloading:', f['Path'], f['Size'], flush=True)
        if f['Size'] > 8 * 1024 * 1024:
            subprocess.run([sys.executable, str(Path(__file__).with_name('download_resumable.py')), url, str(path), '--workers', '16', '--sha256', f['Sha256']], check=True)
        else:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != f['Sha256']:
                raise RuntimeError('Model checksum mismatch: ' + f['Path'])
            path.write_bytes(data)
    print('MODEL VERIFIED', output, flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--model', choices=['0.6B', '1.7B'], default='1.7B')
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    download(args.model, args.output or Path(__file__).resolve().parents[1] / 'models' / ('Qwen3-ASR-' + args.model))
