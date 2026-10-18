from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import sys
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application, load_config, make_handler
from workbuddy.run import save


class HttpPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = load_config(Path(__file__).resolve().parents[1] / 'config.example.json')
        config['automation'] = {'enabled': True}
        config['printing'] = {'enabled': False}
        self.app = Application(config, self.root / 'config.json')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp.cleanup()

    def post(self, path, body, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-App-Token'] = self.app.token
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), headers=headers)
        return urllib.request.urlopen(request)

    def test_task_creation_requires_current_page_token(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/tasks', {'text': '鱼档海报'}, token=False)
        self.assertEqual(error.exception.code, 403)
        with self.post('/api/tasks', {'text': '鱼档海报'}) as response:
            task = json.load(response)['task_id']
        request = json.loads((self.app.pipeline.jobs / task / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['transcript'], '鱼档海报')
        self.assertIn('progress_path', request)

    def test_image_http_only_serves_validated_delivery(self):
        task = self.app.pipeline.create('测试鱼档海报')
        job = self.app.pipeline.jobs / task
        Image.new('RGB', (100, 150)).save(job / 'out/poster.png')
        save(job / 'result.json', {'schema_version': 1, 'task_id': task, 'intent': 'poster',
             'status': 'completed', 'questions': [], 'files': [{'kind': 'poster', 'path': 'out/poster.png'}]})
        with urllib.request.urlopen(self.url + f'/api/jobs/{task}/image?path=out/poster.png') as response:
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertTrue(response.read().startswith(b'\x89PNG'))
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url + f'/api/jobs/{task}/image?path=../request.json')
        self.assertEqual(error.exception.code, 404)

    def test_oversize_or_empty_text_not_enqueued(self):
        for text in ('', 'x' * 12001):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post('/api/tasks', {'text': text})
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(list(self.app.pipeline.jobs.glob('*/request.json')), [])


if __name__ == '__main__':
    unittest.main()
