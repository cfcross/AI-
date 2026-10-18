import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from workbuddy.desktop import WorkBuddyDesktop
from workbuddy.protocol import create_task
from workbuddy.run import CreditGuard, read, save


class Control:
    def __init__(self, kind, name, children=()):
        self.element_info = SimpleNamespace(control_type=kind, name=name)
        self.children = children
        self.clicked = 0

    def descendants(self): return self.children
    def is_visible(self): return True
    def is_enabled(self): return True
    def invoke(self): self.clicked += 1


class CreditTests(unittest.TestCase):
    def test_only_exact_credit_dialog_for_current_task_is_confirmed(self):
        button = Control('Button', '1 确认')
        other = Control('Button', '2 本次会话始终允许')
        dialog = Control('Custom', '确认开始生成图片？', [
            Control('Text', '产生积分消耗'),
            Control('Text', '当前模型生成图片将消耗额外积分，每张图片约 5-10积分，请谨慎操作'),
            button, other])
        adapter = object.__new__(WorkBuddyDesktop)
        adapter.controls = Mock(return_value=[dialog])
        adapter.message_visible = Mock(return_value=True)
        adapter.activate = Mock()
        self.assertTrue(adapter.confirm_generation_credit('task-1'))
        self.assertEqual(button.clicked, 1)
        self.assertEqual(other.clicked, 0)
        adapter.message_visible.return_value = False
        self.assertFalse(adapter.confirm_generation_credit('other-task'))
        adapter.message_visible.return_value = True
        dialog.element_info.name = '允许执行命令？'
        self.assertFalse(adapter.confirm_generation_credit('task-1'))
        dialog.element_info.name = '确认开始生成图片？'
        dialog.children = [button]
        self.assertFalse(adapter.confirm_generation_credit('task-1'))
        self.assertEqual(button.clicked, 1)

    def test_ambiguous_or_changed_dialog_is_not_clicked(self):
        button = Control('Button', '1 确认')
        adapter = object.__new__(WorkBuddyDesktop)
        adapter.activate = Mock()
        adapter.generation_credit_button = Mock(side_effect=[button, None])
        self.assertFalse(adapter.confirm_generation_credit('task-1'))
        self.assertEqual(button.clicked, 0)

    def test_requires_config_and_task_authorization_plus_confirmed_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {'generation': {'credit_authorized': True}}
            job = create_task('测试海报', Path(temp), generation_config=config)
            adapter = Mock()
            adapter.confirm_generation_credit.return_value = True
            CreditGuard({}, config, adapter).tick(job)  # prepared is not submitted
            adapter.confirm_generation_credit.assert_not_called()
            state = read(job / 'state.json')
            state.update(state='submitted', submitted=True)
            save(job / 'state.json', state)
            CreditGuard({}, {}, adapter).tick(job)  # local revocation wins
            adapter.confirm_generation_credit.assert_not_called()
            guard = CreditGuard({}, config, adapter)
            guard.tick(job)
            guard.tick(job)  # throttled
            adapter.confirm_generation_credit.assert_called_once_with(job.name)
            self.assertEqual(read(job / 'credit-confirmations.json')['count'], 1)
            request = read(job / 'request.json')
            request['generation_policy']['credit_authorized'] = False
            save(job / 'request.json', request)
            CreditGuard({}, config, adapter).tick(job)
            self.assertEqual(adapter.confirm_generation_credit.call_count, 1)


if __name__ == '__main__':
    unittest.main()
