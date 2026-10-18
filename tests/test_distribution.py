from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.demo_server import make_handler, snapshot, STAGES
from scripts.install_skill import install
from scripts import package_release


class DistributionTests(unittest.TestCase):
    def test_demo_readable_but_cannot_trigger_hardware(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler('completed'))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            for route in ('/', '/showcase', '/demo-image.svg'):
                with urllib.request.urlopen(url+route) as response:
                    self.assertEqual(response.status, 200)
            with urllib.request.urlopen(url+'/api/state') as response:
                state = json.load(response)
            self.assertFalse(state['pipeline']['auto_print'])
            self.assertEqual(state['pipeline']['tasks'][0]['state'], 'completed')
            for route in ('/api/tasks', '/api/jobs/demo/print', '/api/stop'):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url+route, data=b'{}', method='POST'))
                self.assertEqual(error.exception.code, 409)
            for stage in STAGES:
                self.assertIn('history', snapshot(stage))
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_package_excludes_runtime_secrets_and_includes_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for rel in ('config.example.json', 'config.json', 'data/record.json', '.env',
                        'skills/market-stall-demo/SKILL.md', 'skills/market-stall-demo/_user_meta.json',
                        'workbuddy/desktop-profile.json', 'workbuddy/desktop-profile.example.json',
                        'runtime/node.exe', 'scripts/demo_server.py'):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            with patch.object(package_release, 'ROOT', root):
                paths = {p.relative_to(root).as_posix() for p in package_release.source_files()}
            self.assertEqual(paths, {'config.example.json', 'skills/market-stall-demo/SKILL.md',
                                     'workbuddy/desktop-profile.example.json', 'scripts/demo_server.py'})

    def test_skill_install_backs_up_previous_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            src = root / 'skills/market-stall-demo'
            src.mkdir(parents=True)
            (src/'SKILL.md').write_text('new')
            target = Path(temp)/'installed'
            target.mkdir()
            (target/'SKILL.md').write_text('old')
            with patch('scripts.install_skill.ROOT', root):
                install(target)
            self.assertEqual((target/'SKILL.md').read_text(), 'new')
            self.assertEqual(next((root/'data/skill-backups').glob('*/SKILL.md')).read_text(), 'old')
