import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import sys
import subprocess
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbuddy.pipeline import Pipeline
from workbuddy.protocol import create_task
from workbuddy.run import read, save


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {'storage': {'directory': 'data'}, 'automation': {'enabled': True},
                       'printing': {'enabled': True, 'printer_name': 'Test Canon'}}
        self.calls = []
        self.pipeline = Pipeline(self.root, self.config, self.root / 'config.json', self.fake_print)

    def tearDown(self):
        self.temp.cleanup()

    def fake_print(self, image, printer, receipt, name):
        self.calls.append(str(image))
        save(receipt, {'status': 'spooled'})

    def make_job(self, status='completed', count=1):
        job = create_task('测试鱼档，鲈鱼35元一斤，做价格牌', self.pipeline.jobs)
        files = []
        for i in range(count):
            Image.new('RGB', (100, 150), ('red', 'blue')[i % 2]).save(job / f'out/{i}.png')
            files.append({'kind': 'price_board', 'path': f'out/{i}.png'})
        save(job / 'result.json', {'schema_version': 1, 'task_id': job.name, 'status': status,
             'intent': 'price_board', 'questions': ['缺少价格'] if status == 'needs_input' else [],
             'message': '测试', 'files': files if status == 'completed' else []})
        return job

    def test_completion_prints_once_even_after_restart(self):
        job = self.make_job()
        self.pipeline.scan()
        self.pipeline.scan()
        restarted = Pipeline(self.root, self.config, self.root / 'config.json', self.fake_print)
        restarted.scan()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read(job / 'print.json')['state'], 'spooled')
        self.assertEqual(restarted.snapshot()['tasks'][0]['printing']['state'], 'spooled')

    def test_existing_tasks_are_shown_but_not_auto_printed(self):
        job = self.make_job()
        request = read(job / 'request.json')
        request['created_at'] = '2020-01-01T00:00:00+00:00'
        save(job / 'request.json', request)
        self.pipeline.scan()
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.pipeline.snapshot()['tasks'][0]['files']), 1)

    def test_needs_input_and_invalid_result_never_print(self):
        job = self.make_job(status='needs_input')
        self.pipeline.scan()
        self.assertFalse(self.calls)
        result = read(job / 'result.json')
        result.update(status='completed', questions=[], files=[{'kind': 'price_board', 'path': '../secret.png'}])
        save(job / 'result.json', result)
        self.pipeline.scan()
        self.assertFalse(self.calls)
        self.assertIn('validation_error', self.pipeline.snapshot()['tasks'][0])

    def test_print_crash_never_retries_automatically(self):
        job = self.make_job()
        def crash(*args):
            self.calls.append('called')
            raise RuntimeError('lost printer response')
        self.pipeline.printer_runner = crash
        self.pipeline.scan()
        self.pipeline.scan()
        self.assertEqual(self.calls, ['called'])
        self.assertEqual(read(job / 'print.json')['state'], 'submission_unknown')
        with self.assertRaises(ValueError):
            self.pipeline.request_print(job.name, ['out/0.png'])

    def test_multiple_variants_require_selection_and_manual_print_deduplicates(self):
        job = self.make_job(count=2)
        self.pipeline.scan()
        self.assertEqual(read(job / 'print.json')['state'], 'selection_needed')
        self.assertFalse(self.calls)
        self.pipeline.request_print(job.name, ['out/1.png'])
        self.pipeline.scan()
        self.pipeline.scan()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0].endswith('1.png'))

    def test_web_task_carries_credit_policy(self):
        self.config['generation'] = {'credit_authorized': True, 'auto_print_final_pages': True}
        task_id = self.pipeline.create('测试鱼档海报')
        policy = read(self.pipeline.jobs / task_id / 'request.json')['generation_policy']
        self.assertTrue(policy['credit_authorized'])
        self.assertTrue(policy['auto_print_final_pages'])

    def test_final_pages_print_without_selection_only_with_both_config_and_task_policy(self):
        job = self.make_job(count=2)
        request = read(job / 'request.json')
        request['generation_policy']['auto_print_final_pages'] = True
        save(job / 'request.json', request)
        result = read(job / 'result.json')
        result['final_selection'] = True
        self.assertEqual(self.pipeline.print_task(job, result)['state'], 'selection_needed')
        self.config['generation'] = {'auto_print_final_pages': True}
        result['final_selection'] = False
        self.assertEqual(self.pipeline.print_task(job, result)['state'], 'selection_needed')
        result['final_selection'] = True
        self.assertEqual(self.pipeline.print_task(job, result)['state'], 'spooled')
        self.pipeline.print_task(job, result)
        self.assertEqual(len(self.calls), 2)

    def test_manual_legacy_receipt_prevents_reprint(self):
        job = self.make_job()
        save(job / 'print-manual-a4-color-1.json', {'status': 'spooled'})
        self.pipeline.scan()
        self.assertFalse(self.calls)

    def test_late_completed_result_replaces_needs_input(self):
        job = self.make_job(status='needs_input')
        self.pipeline.scan()
        result = read(job / 'result.json')
        result.update(status='completed', questions=[], files=[{'kind': 'price_board', 'path': 'out/0.png'}])
        save(job / 'result.json', result)
        self.pipeline.scan()
        self.assertEqual(len(self.calls), 1)

    def test_progress_is_scoped_to_current_task(self):
        job = self.make_job(status='needs_input')
        save(job / 'progress.json', {'task_id': 'wrong', 'stage': 'completed'})
        self.pipeline.scan()
        self.assertNotIn('progress', self.pipeline.snapshot()['tasks'][0])
        save(job / 'progress.json', {'task_id': job.name, 'stage': 'rendering', 'message': '排版'})
        self.pipeline.scan()
        self.assertEqual(self.pipeline.snapshot()['tasks'][0]['progress']['stage'], 'rendering')

    def test_image_route_rejects_other_files_and_path_traversal(self):
        job = self.make_job()
        self.assertTrue(self.pipeline.image_path(job.name, 'out/0.png').is_file())
        with self.assertRaises(ValueError):
            self.pipeline.image_path(job.name, '../request.json')
        with self.assertRaises(ValueError):
            self.pipeline.job_path('../jobs')

    def test_both_outputs_print_once_each(self):
        job = self.make_job(count=2)
        result = read(job / 'result.json')
        result['intent'] = 'both'
        result['files'][1]['kind'] = 'poster'
        save(job / 'result.json', result)
        self.pipeline.scan()
        self.assertEqual(len(self.calls), 2)

    def test_default_duplex_ledger_records_two_sides_same_image(self):
        job = self.make_job()
        self.pipeline.scan()
        ledger = read(job / 'print.json')
        self.assertEqual(ledger['duplex'], 'LongEdge')
        self.assertEqual(ledger['layout'], 'Cover')
        self.assertEqual(ledger['back_content'], 'same_image')
        self.assertEqual(ledger['pages_per_image'], 2)

    def test_driver_command_explicitly_requests_duplex_and_cover(self):
        with patch('workbuddy.pipeline.shutil.which', return_value='powershell.exe'), \
             patch('workbuddy.pipeline.subprocess.run') as runner:
            runner.return_value.returncode = 0
            self.pipeline.run_printer(self.root / 'x.png', 'Canon', self.root / 'receipt.json', 'test')
            arguments = runner.call_args.args[0]
            self.assertEqual(arguments[arguments.index('-DuplexMode')+1], 'LongEdge')
            self.assertEqual(arguments[arguments.index('-Layout')+1], 'Cover')

    def test_driver_preparation_timeout_is_named_and_never_blindly_reprinted(self):
        job = self.make_job()
        self.pipeline.printer_runner = self.pipeline.run_printer
        def stuck(command, **kwargs):
            receipt = Path(command[command.index('-ReceiptPath') + 1])
            save(receipt, {'status': 'preparing', 'stage': 'checking_printer'})
            raise subprocess.TimeoutExpired(command, 90)
        with patch('workbuddy.pipeline.shutil.which', return_value='powershell.exe'), \
             patch('workbuddy.pipeline.subprocess.run', side_effect=stuck) as runner:
            self.pipeline.scan()
            self.pipeline.scan()
            self.assertEqual(runner.call_count, 1)
        ledger = read(job / 'print.json')
        self.assertIn('驱动准备超时', ledger['message'])
        self.assertNotIn('Command ', ledger['message'])
        self.assertIn('checking_printer', ledger['error_detail'])

    def test_snapshot_reads_live_print_status_while_scan_is_blocked(self):
        job = self.make_job()
        def printing(*args):
            self.assertEqual(self.pipeline.snapshot()['tasks'][0]['printing']['state'], 'submitting')
            self.fake_print(*args)
        self.pipeline.printer_runner = printing
        self.pipeline.scan()
        self.assertEqual(self.pipeline.snapshot()['tasks'][0]['printing']['state'], 'spooled')

    def enable_ringing(self, handler):
        self.config['notifications'] = {'enabled': True}
        self.pipeline = Pipeline(self.root, self.config, self.root / 'config.json', self.fake_print, handler)

    def test_ring_on_completion_only_once_after_restart(self):
        rings = []
        def ring(task):
            rings.append(task)
            return True
        self.enable_ringing(ring)
        job = self.make_job()
        self.pipeline.scan()
        self.pipeline.notification_event({'task_id': job.name, 'state': 'done', 'message': 'done'})
        self.pipeline.scan()
        restarted = Pipeline(self.root, self.config, self.root / 'config.json', self.fake_print, ring)
        restarted.scan()
        self.assertEqual(rings, [job.name])

    def test_busy_line_defers_and_historical_completed_jobs_do_not_ring(self):
        self.make_job()
        rings = []
        self.enable_ringing(lambda task: rings.append(task) or False)
        self.pipeline.scan()
        self.assertEqual(rings, [])
        job = self.make_job()
        self.pipeline.scan()
        self.assertEqual(read(job / 'notification.json')['state'], 'pending')
        self.pipeline.ring_handler = lambda task: rings.append(task) or True
        record = read(job / 'notification.json'); record['retry_after'] = 0
        save(job / 'notification.json', record)
        self.pipeline.scan()
        self.pipeline.scan()
        self.assertEqual(rings, [job.name, job.name])

    def test_needs_input_cannot_ring(self):
        rings = []
        self.enable_ringing(lambda task: rings.append(task) or True)
        self.make_job(status='needs_input')
        self.pipeline.scan()
        self.assertEqual(rings, [])


if __name__ == '__main__':
    unittest.main()
