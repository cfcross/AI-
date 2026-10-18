import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbuddy.desktop import WorkBuddyDesktop
from workbuddy.run import DisplayGuard


def control(kind, name, selected=False):
    return Mock(element_info=SimpleNamespace(control_type=kind, name=name),
                is_visible=Mock(return_value=True), is_selected=Mock(return_value=selected))


class DisplayTests(unittest.TestCase):
    def test_restore_only_our_selected_artifact_in_full_view(self):
        adapter = object.__new__(WorkBuddyDesktop)
        adapter.window = SimpleNamespace(handle=123)
        adapter.connect = Mock()
        adapter.show_display = Mock()
        full = control('Button', '退出全屏')
        for tabs, expected in [
            ([full, control('TabItem', 'task-uuid_poster.png 关闭', True)], True),
            ([full, control('TabItem', 'task-uuid_poster.png 关闭', False)], False),
            ([full, control('TabItem', 'private.png 关闭', True)], False),
            ([control('TabItem', 'task-uuid_poster.png 关闭', True)], False),
        ]:
            adapter.controls = Mock(return_value=tabs)
            with patch('win32gui.GetForegroundWindow', return_value=123):
                self.assertEqual(adapter.restore_generated_preview('url', {'task-uuid'}), expected)
        self.assertEqual(adapter.show_display.call_count, 1)
        with patch('win32gui.GetForegroundWindow', return_value=999):
            self.assertFalse(adapter.restore_generated_preview('url', {'task-uuid'}))

    def test_guard_keeps_checking_after_completion_and_throttles(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            job = data / 'workbuddy/jobs/task-uuid'
            job.mkdir(parents=True)
            (job / 'state.json').write_text('{"state":"completed"}')
            adapter = Mock()
            guard = DisplayGuard(data, {'show_display_after_send': True, 'display_url': 'url'}, adapter)
            with patch('workbuddy.run.time.monotonic', return_value=100):
                guard.tick()
                guard.tick()
            adapter.restore_generated_preview.assert_called_once_with('url', {'task-uuid'})
            with patch('workbuddy.run.time.monotonic', return_value=103):
                guard.tick()
            self.assertEqual(adapter.restore_generated_preview.call_count, 2)

    def test_display_failure_does_not_escape_into_task_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = Mock()
            adapter.restore_generated_preview.side_effect = RuntimeError('UI unavailable')
            guard = DisplayGuard(Path(directory), {'show_display_after_send': True, 'display_url': 'url'}, adapter)
            guard.tick()
            self.assertGreater(guard.next_check, 0)


if __name__ == '__main__':
    unittest.main()
