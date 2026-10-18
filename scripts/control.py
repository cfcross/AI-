"""Graceful local shutdown; the random token comes from this application's data folder."""
import json
import argparse
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--config', type=Path, default=root / 'config.json')
args = parser.parse_args()
root = args.config.resolve().parent
config = json.loads(args.config.read_text(encoding='utf-8-sig'))
path = root / config['storage']['directory'] / 'service.json'
if not path.exists():
    raise SystemExit('No running service record found.')
control = json.loads(path.read_text(encoding='utf-8'))
req = urllib.request.Request(f"http://127.0.0.1:{control['port']}/api/stop", method='POST', data=b'',
                             headers={'Authorization': 'Bearer ' + control['token']})
with urllib.request.urlopen(req, timeout=5) as response:
    print('Application shutdown requested:', response.status)
