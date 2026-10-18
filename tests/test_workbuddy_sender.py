import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbuddy.protocol import create_task
from workbuddy.run import send_job, read, save, watch_loop
from workbuddy.desktop import DesktopBlocked


class FakeDesktop:
    def __init__(self, fail_at=None, confirmed=True):
        self.fail_at, self.confirmed, self.submissions = fail_at, confirmed, 0

    def prepare_input(self, prompt, resume_skill=False):
        if self.fail_at == 'prepare':
            raise RuntimeError('skill missing or draft present')

    def submit(self, prompt):
        self.submissions += 1
        if self.fail_at == 'submit':
            raise RuntimeError('lost UI after click')

    def confirm_submission(self, task_id):
        return self.confirmed


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job = create_task('测试鱼档，做价格牌，鲈鱼每斤35元', self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_skill_or_draft_failure_cannot_send(self):
        adapter = FakeDesktop(fail_at='prepare')
        self.assertEqual(send_job(self.job, {}, adapter=adapter)['state'], 'waiting_desktop')
        self.assertEqual(adapter.submissions, 0)

    def test_confirmation_required_and_no_retry_of_uncertain_click(self):
        adapter = FakeDesktop(confirmed=False)
        self.assertEqual(send_job(self.job, {}, adapter=adapter)['state'], 'submission_unknown')
        send_job(self.job, {}, adapter=adapter)
        self.assertEqual(adapter.submissions, 1)

    def test_send_exception_keeps_uncertainty(self):
        adapter = FakeDesktop(fail_at='submit')
        self.assertEqual(send_job(self.job, {}, adapter=adapter)['state'], 'submission_unknown')

    def test_confirmed_task_not_sent_again(self):
        adapter = FakeDesktop()
        self.assertTrue(send_job(self.job, {}, adapter=adapter)['submitted'])
        send_job(self.job, {}, adapter=adapter)
        self.assertEqual(adapter.submissions, 1)

    def test_minimize_failure_cannot_repeat_confirmed_send(self):
        adapter = FakeDesktop()
        def release():
            raise RuntimeError('window moved')
        adapter.release_foreground = release
        self.assertEqual(send_job(self.job, {}, adapter=adapter)['state'], 'submitted')
        send_job(self.job, {}, adapter=adapter)
        self.assertEqual(adapter.submissions, 1)

    def test_display_opens_only_after_confirmed_send_and_does_not_minimize(self):
        adapter = FakeDesktop()
        events = []
        adapter.show_display = lambda url: events.append(('display', url))
        adapter.release_foreground = lambda: events.append(('minimize',))
        profile = {'show_display_after_send': True, 'display_url': 'http://127.0.0.1:8766/showcase'}
        self.assertEqual(send_job(self.job, profile, adapter=adapter)['state'], 'submitted')
        self.assertEqual(events, [('display', profile['display_url'])])

    def test_display_failure_does_not_resend_task(self):
        adapter = FakeDesktop()
        def fail(url):
            raise RuntimeError('preview unavailable')
        adapter.show_display = fail
        profile = {'show_display_after_send': True, 'display_url': 'http://127.0.0.1:8766/showcase'}
        self.assertTrue(send_job(self.job, profile, adapter=adapter)['submitted'])
        send_job(self.job, profile, adapter=adapter)
        self.assertEqual(adapter.submissions, 1)

    def test_crash_after_sending_persisted_never_retries(self):
        path = self.job / 'state.json'
        state = read(path)
        state['state'] = 'sending'
        save(path, state)
        adapter = FakeDesktop()
        self.assertEqual(send_job(self.job, {}, adapter=adapter)['state'], 'submission_unknown')
        self.assertEqual(adapter.submissions, 0)

    def test_foreground_failure_is_retryable_but_draft_is_not(self):
        adapter = FakeDesktop()
        with patch.object(adapter, 'prepare_input', side_effect=DesktopBlocked('focus', retryable=True)):
            self.assertTrue(send_job(self.job, {}, adapter=adapter)['automatic_retry'])
        with patch.object(adapter, 'prepare_input', side_effect=DesktopBlocked('draft')):
            self.assertFalse(send_job(self.job, {}, adapter=adapter)['automatic_retry'])
        self.assertEqual(adapter.submissions, 0)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.job = create_task('鲈鱼每斤35元', self.data / 'workbuddy/jobs')
        self.passes = 0

    def tearDown(self):
        self.temp.cleanup()

    def stop_after_three(self):
        self.passes += 1
        if self.passes > 3:
            raise KeyboardInterrupt

    def test_focus_failure_keeps_watcher_alive_and_retries_once_before_success(self):
        def send(job, profile):
            state = read(job / 'state.json')
            if sender.call_count == 1:
                state.update(state='waiting_desktop', automatic_retry=True)
            else:
                state.update(state='submitted', submitted=True)
            save(job / 'state.json', state)
            return state

        def finish(job, timeout, check_stop):
            state = read(job / 'state.json')
            state['state'] = 'needs_input'
            save(job / 'state.json', state)
            return state

        with patch('workbuddy.run.send_job', side_effect=send) as sender, \
             patch('workbuddy.run.monitor', side_effect=finish), \
             patch('workbuddy.run.time.sleep'):
            with self.assertRaises(KeyboardInterrupt):
                watch_loop(self.data, self.data, {'desktop_retry_seconds': 0}, self.stop_after_three)
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(read(self.job / 'state.json')['state'], 'needs_input')
        self.assertEqual(read(self.data / 'workbuddy/watch-status.json')['phase'], 'listening')

    def test_uncertain_submission_or_draft_keeps_alive_without_sending(self):
        for blocked_state in ('submission_unknown', 'waiting_desktop'):
            self.passes = 0
            state = read(self.job / 'state.json')
            state.update(state=blocked_state, automatic_retry=False)
            save(self.job / 'state.json', state)
            with patch('workbuddy.run.send_job') as sender, patch('workbuddy.run.time.sleep'):
                with self.assertRaises(KeyboardInterrupt):
                    watch_loop(self.data, self.data, {}, self.stop_after_three)
                sender.assert_not_called()
            self.assertEqual(read(self.data / 'workbuddy/watch-status.json')['phase'], 'needs_attention')


if __name__ == '__main__':
    unittest.main()
