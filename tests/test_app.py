import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Application, load_config
from asr_engine import choose_device


class FakeTorch:
    float16, float32, bfloat16 = 'half', 'single', 'bfloat'
    class cuda:
        @staticmethod
        def is_available():
            return False


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = load_config(Path(__file__).resolve().parents[1] / 'config.example.json')
        self.config['storage']['directory'] = 'data'
        self.app = Application(self.config, self.root / 'config.json')
        audio = self.app.data / 'recordings/example.wav'
        audio.parent.mkdir()
        audio.write_bytes(b'placeholder')
        self.event = {'event': 'hangup', 'id': 'example', 'file': str(audio), 'reason': 'hangup', 'seconds': 3}

    def tearDown(self):
        self.temp.cleanup()

    def test_normal_hangup_queues_exactly_once(self):
        self.app.on_event(self.event)
        self.app.on_event(self.event)
        self.assertEqual(self.app.jobs.qsize(), 1)
        self.assertEqual(self.app.snapshot()['phase'], 'transcribing')

    def test_abnormal_disconnect_never_transcribes(self):
        self.app.on_event(dict(self.event, reason='media_timeout'))
        self.assertTrue(self.app.jobs.empty())
        result = self.app.snapshot()['history'][0]
        self.assertEqual(result['text'], '')
        self.assertIn('media_timeout', result['error'])

    def test_result_persisted_and_ready_for_next_call(self):
        self.app.on_event(self.event)
        self.app.engine = type('FakeEngine', (), {'transcribe': lambda _, path: {'text': '鲈鱼每斤三十五元', 'language': 'Chinese'}})()
        self.app.process_recording(self.app.jobs.get_nowait())
        self.assertEqual(self.app.snapshot()['phase'], 'ready')
        saved = json.loads((self.app.data / 'transcripts/example.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['text'], '鲈鱼每斤三十五元')
        self.assertEqual(saved['file'], str(Path('recordings/example.wav')))

    def test_model_error_recovers_for_next_call(self):
        self.app.on_event(self.event)
        def fail(*_):
            raise RuntimeError('GPU out of memory')
        self.app.engine = type('BrokenEngine', (), {'transcribe': fail})()
        self.app.process_recording(self.app.jobs.get_nowait())
        self.assertEqual(self.app.snapshot()['phase'], 'ready')
        self.assertIn('GPU out of memory', self.app.snapshot()['history'][0]['error'])

    def test_correction_is_saved_before_watcher_and_display_receive_text(self):
        self.app.finish_record(self.event, text='东兴班每斤八十八元', raw_text='東興班每斤八十八元')
        record = self.app.snapshot()['history'][0]
        self.assertEqual(record['text'], '东星斑每斤八十八元')
        self.assertEqual(record['asr_text'], '东兴班每斤八十八元')
        self.assertEqual(record['raw_text'], '東興班每斤八十八元')
        saved = json.loads((self.app.data / 'transcripts/example.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['correction']['edits'][0]['replacement'], '东星斑')
        self.assertEqual((self.app.data / 'transcripts/example.txt').read_text(encoding='utf-8'), record['text'])

    def test_no_gpu_auto_uses_float32_cpu(self):
        self.assertEqual(choose_device(FakeTorch), ('cpu', 'single', 'float32'))
        with self.assertRaises(RuntimeError):
            choose_device(FakeTorch, 'cuda:0')

    def test_quiet_audio_is_not_submitted_to_model(self):
        import numpy as np
        import wave
        from asr_engine import AsrEngine
        engine = AsrEngine.__new__(AsrEngine)
        engine.np, engine.torch, engine.config = np, None, self.config['asr']
        path = self.root / 'silent.wav'
        with wave.open(str(path), 'wb') as out:
            out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            out.writeframes(bytes(16000))
        self.assertTrue(engine.transcribe(path)['empty'])


if __name__ == '__main__':
    unittest.main()
