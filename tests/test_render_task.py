from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image
from workbuddy.protocol import create_task
from workbuddy.render_task import render, complete, prepare_items
from workbuddy.run import read, save
from workbuddy.timing import observe


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.job = create_task('鱼档海报', self.root, skill_path=str(self.root))
        (self.job / 'gen').mkdir(exist_ok=True)
        Image.new('RGB', (100, 150), 'blue').save(self.job / 'gen/bg.png')
        self.request = read(self.job / 'request.json')

    def tearDown(self):
        self.tmp.cleanup()

    def design(self):
        return {'intent': 'poster', 'posters': [{'name': '<鱼&档>', 'cat': '水产', 'bg': 'gen/bg.png'}]}

    def fake_engine(self):
        def build(item, out, chrome, scale):
            self.assertEqual(item['name'], '&lt;鱼&amp;档&gt;')
            self.assertEqual(item['top_cn'], ' ')
            self.assertEqual(item['side1'], ' ')
            output = Path(out) / 'final.png'
            Image.new('RGB', (100, 150), 'blue').save(output)
            return 'unused.html', str(output)
        return SimpleNamespace(build_one=build, find_chrome=lambda: 'browser')

    def test_render_does_not_publish_until_visual_approval_and_delivery_is_idempotent(self):
        save(self.job / 'design.json', self.design())
        with patch('workbuddy.render_task.engine', return_value=self.fake_engine()):
            result = render(self.job, 'design.json')
        self.assertEqual(len(result['preview']), 1)
        self.assertFalse((self.job / 'result.json').exists())
        with self.assertRaises(ValueError):
            complete(self.job)
        complete(self.job, True)
        stamp = (self.job / 'result.json').stat().st_mtime_ns
        complete(self.job, True)
        self.assertEqual((self.job / 'result.json').stat().st_mtime_ns, stamp)

    def test_changed_image_cannot_be_delivered(self):
        save(self.job / 'design.json', self.design())
        with patch('workbuddy.render_task.engine', return_value=self.fake_engine()):
            render(self.job, 'design.json')
        Image.new('RGB', (100, 150), 'red').save(self.job / 'out/final.png')
        with self.assertRaises(ValueError):
            complete(self.job, True)
        self.assertFalse((self.job / 'result.json').exists())

    def test_missing_background_foreign_file_and_missing_price_unit_are_rejected(self):
        for source in ('gen/missing.png', '../outside.png'):
            Image.new('RGB', (100, 150)).save(self.root / 'outside.png')
            design = self.design()
            design['posters'][0]['bg'] = source
            with self.assertRaises((ValueError, OSError)):
                prepare_items(self.job, self.request, design)
        price = {'intent': 'price_board', 'price_boards': [{'name': '鱼档', 'groups': [
            {'items': [{'n': '鲈鱼', 'p': '35'}]}]}]}
        with self.assertRaises(ValueError):
            prepare_items(self.job, self.request, price)

    def test_timings_use_file_times_not_agent_clock(self):
        save(self.job / 'progress.json', {'task_id': self.job.name, 'stage': 'rendering', 'updated_at': '2099-01-01'})
        result = observe(self.job, self.request, {'submitted_at': self.request['created_at']})
        self.assertEqual(result['background_count'], 1)
        self.assertNotIn('2099', result['stages'][0]['at'])
        observe(self.job, self.request, {})
        self.assertEqual(len(read(self.job / 'timings.json')['stages']), 1)


if __name__ == '__main__':
    unittest.main()
