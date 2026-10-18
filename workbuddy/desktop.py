"""Windows WorkBuddy UI adapter. All selectors are scoped to the configured process."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import time


class DesktopBlocked(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def process_path(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ''
    try:
        length = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(length.value)
        return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) else ''
    finally:
        kernel.CloseHandle(handle)


class WorkBuddyDesktop:
    def __init__(self, profile):
        if os.name != 'nt':
            raise DesktopBlocked('桌面输入仅支持 Windows')
        from pywinauto import Desktop
        self.desktop = Desktop(backend='uia')
        self.profile = profile
        target = profile['target']
        self.exe = Path(os.path.expandvars(target.get('executable_path') or
            str(Path(os.environ['LOCALAPPDATA']) / target['executable_relative_to_localappdata']))).resolve()
        self.window = None
        self.previous_foreground = None

    def connect(self):
        deadline = time.monotonic() + 3
        while True:
            try:
                return self._connect_once()
            except DesktopBlocked:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)

    def _connect_once(self):
        candidates = []
        for window in self.desktop.windows(visible_only=False):
            if Path(process_path(window.process_id())).resolve() == self.exe:
                if not self.profile['target'].get('window_title_pattern') or re.fullmatch(self.profile['target']['window_title_pattern'], window.window_text()):
                    candidates.append(window)
        if len(candidates) != 1:
            raise DesktopBlocked(f'需要唯一 WorkBuddy 主窗口，当前匹配 {len(candidates)} 个；请打开并登录指定客户端')
        self.window = candidates[0]
        return self.window

    def controls(self):
        if self.window is None:
            self.connect()
        return [self.window, *self.window.descendants()]

    @staticmethod
    def text(control):
        # Never use a placeholder/name as the actual content of an editable field.
        try:
            return control.iface_value.CurrentValue
        except Exception:
            try:
                return control.iface_text.DocumentRange.GetText(-1)
            except Exception:
                raise DesktopBlocked('控件未暴露可读文本，不能验证输入内容')

    def inspect(self):
        records = []
        for control in self.controls():
            info = control.element_info
            record = {'type': info.control_type, 'name': info.name, 'auto_id': info.automation_id,
                      'class': info.class_name, 'enabled': control.is_enabled(), 'visible': control.is_visible()}
            if info.control_type == 'Edit':
                try:
                    record['value'] = self.text(control)[:200]
                except DesktopBlocked:
                    record['value'] = None
            records.append(record)
        return {'window': self.window.window_text(), 'process': str(self.exe), 'controls': records}

    def generation_credit_button(self, task_id):
        """Match ONLY the observed image-credit modal inside this task's WorkBuddy."""
        controls = self.controls()
        dialogs = [c for c in controls if c.is_visible()
                   and c.element_info.control_type == 'Custom'
                   and c.element_info.name == '确认开始生成图片？']
        if len(dialogs) != 1 or not self.message_visible(task_id):
            return None
        children = [c for c in dialogs[0].descendants() if c.is_visible()]
        texts = {c.element_info.name for c in children if c.element_info.control_type == 'Text'}
        if '产生积分消耗' not in texts or not any(
            text.startswith('当前模型生成图片将消耗额外积分') for text in texts
        ):
            return None
        buttons = [c for c in children if c.element_info.control_type == 'Button'
                   and c.element_info.name == '1 确认' and c.is_enabled()]
        return buttons[0] if len(buttons) == 1 else None

    def confirm_generation_credit(self, task_id):
        if self.generation_credit_button(task_id) is None:
            return False
        # UIA reports the chat controls visible even underneath the fullscreen
        # showcase. Invoke the exact button; a coordinate click hits the web page.
        button = self.generation_credit_button(task_id)
        if button is None:
            return False
        button.invoke()
        return True

    def find(self, selector):
        deadline = time.monotonic() + 3
        while True:
            try:
                return self._find_once(selector)
            except DesktopBlocked:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)

    def _find_once(self, selector):
        if not selector:
            raise DesktopBlocked('尚未配置此步骤的控件选择器')
        matches = []
        for control in self.controls():
            info = control.element_info
            if not control.is_visible():
                continue
            if selector.get('type') and info.control_type != selector['type']:
                continue
            if selector.get('types') and info.control_type not in selector['types']:
                continue
            if 'name' in selector and info.name != selector['name']:
                continue
            if selector.get('name_re') and not re.fullmatch(selector['name_re'], info.name or ''):
                continue
            if selector.get('auto_id') and info.automation_id != selector['auto_id']:
                continue
            matches.append(control)
        if len(matches) != 1:
            raise DesktopBlocked(f'控件必须唯一：{selector}，实际 {len(matches)} 个')
        return matches[0]

    def selected(self, key):
        return self.find(self.profile['target']['selectors'].get(key))

    @staticmethod
    def normalized(text):
        return text.replace('\ufeff', '').replace('\u200b', '').replace('\ufffc', '').replace('\r\n', '\n').strip()

    def composer_text(self):
        text = self.normalized(self.text(self.selected('composer')))
        if text in self.profile.get('empty_placeholders', []):
            return ''
        return text

    def activate(self):
        import win32api
        import win32gui
        import win32process
        self.connect()
        handle = self.window.handle
        if self.previous_foreground is None:
            self.previous_foreground = win32gui.GetForegroundWindow()
        if win32gui.GetForegroundWindow() == handle:
            return
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, 9)
        # UIA SetFocus alone does not reliably activate an Electron top-level window.
        # Ask Win32 to activate it, wait for the asynchronous transition, then retry
        # with a temporary input-thread attachment. Never change global focus policy.
        current_thread = win32api.GetCurrentThreadId()
        for attach in (False, True):
            attached = []
            try:
                if attach:
                    foreground = win32gui.GetForegroundWindow()
                    threads = {win32process.GetWindowThreadProcessId(h)[0]
                               for h in (foreground, handle) if h}
                    for thread in threads - {current_thread}:
                        win32process.AttachThreadInput(current_thread, thread, True)
                        attached.append(thread)
                win32gui.BringWindowToTop(handle)
                win32gui.SetForegroundWindow(handle)
            except Exception:
                pass  # Success is determined by the actual foreground handle below.
            finally:
                for thread in reversed(attached):
                    win32process.AttachThreadInput(current_thread, thread, False)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if win32gui.GetForegroundWindow() == handle:
                    return
                time.sleep(.1)
        raise DesktopBlocked('WorkBuddy 未获得前台焦点，等待自动重试', retryable=True)

    def release_foreground(self):
        """After a confirmed send, let the model work while the kiosk stays visible."""
        import win32gui
        if not self.profile.get('minimize_after_send', False) or self.window is None:
            return
        handle = self.window.handle
        # Do not take focus back if the user has already switched elsewhere.
        if win32gui.GetForegroundWindow() != handle:
            return
        win32gui.ShowWindow(handle, 6)
        previous = self.previous_foreground
        if previous and previous != handle and win32gui.IsWindow(previous):
            try:
                win32gui.SetForegroundWindow(previous)
            except Exception:
                pass  # Minimizing already returns the previous window to view.

    def exit_display(self):
        self.activate()
        buttons = [c for c in self.controls() if c.is_visible() and
                   c.element_info.control_type == 'Button' and c.element_info.name == '退出全屏']
        if len(buttons) == 1:
            buttons[0].click_input()
            time.sleep(.3)

    def show_display(self, url):
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path != '/showcase':
            raise DesktopBlocked('展示地址必须为本机 /showcase 页面')
        self.activate()
        def visible(kind, name):
            return [c for c in self.controls() if c.is_visible() and
                    c.element_info.control_type == kind and c.element_info.name == name]
        # Reuse the existing kiosk tab when a generated file has taken its place.
        tabs = visible('TabItem', 'WorkBuddy · 电话物料展示 关闭')
        if len(tabs) == 1:
            tabs[0].click_input()
            time.sleep(.2)
        # Open a browser tab through observed, named WorkBuddy controls.
        address = visible('Edit', '搜索或输入网址')
        if not address:
            opener = visible('Button', '打开右侧栏')
            if len(opener) == 1:
                opener[0].click_input(); time.sleep(.3)
            browser = visible('Text', '打开浏览器')
            if not browser:
                self.find({'type': 'Button', 'name': '新标签页'}).click_input()
                browser = [self.find({'type': 'Text', 'name': '打开浏览器'})]
            if len(browser) != 1:
                raise DesktopBlocked('浏览器入口不唯一，无法自动打开展示页')
            browser[0].click_input()
            address = [self.find({'type': 'Edit', 'name': '搜索或输入网址'})]
        if len(address) != 1:
            raise DesktopBlocked('浏览器地址栏不唯一')
        control = address[0]
        if self.text(control).rstrip('/') != url.rstrip('/'):
            control.set_focus()
            control.type_keys('^a', set_foreground=False)
            control.type_keys(url, with_spaces=True, pause=.01, set_foreground=False)
            control.type_keys('{ENTER}', set_foreground=False)
        full = visible('Button', '进入全屏')
        if len(full) == 1:
            full[0].click_input()
        # A recognized address is required; no blind repeated tabs/navigation.
        actual = self.text(self.find({'type': 'Edit', 'name': '搜索或输入网址'}))
        if actual.rstrip('/') != url.rstrip('/'):
            raise DesktopBlocked('展示网址未能确认')

    def restore_generated_preview(self, url, task_ids):
        """Only return from our generated artifact tabs while in kiosk full view."""
        import win32gui
        self.connect()
        if win32gui.GetForegroundWindow() != self.window.handle:
            return False
        controls = [c for c in self.controls() if c.is_visible()]
        if not any(c.element_info.control_type == 'Button' and
                   c.element_info.name == '退出全屏' for c in controls):
            return False  # Operator has deliberately opened the conversation.
        tabs = [c for c in controls if c.element_info.control_type == 'TabItem']
        for tab in tabs:
            name = tab.element_info.name or ''
            if any(name.startswith(task_id) for task_id in task_ids) and tab.is_selected():
                self.show_display(url)
                return True
        return False

    def focus_composer(self):
        import win32gui
        control = self.selected('composer')
        control.set_focus()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if win32gui.GetForegroundWindow() == self.window.handle and control.element_info.element.CurrentHasKeyboardFocus:
                return control
            time.sleep(.1)
        raise DesktopBlocked('输入框焦点无法确认，暂停输入')

    def click(self, key):
        self.activate()
        control = self.selected(key)
        if not control.is_enabled():
            raise DesktopBlocked(f'{key} 控件不可操作')
        control.click_input()

    def open_skill(self):
        self.activate()
        # Protect drafts in the currently active conversation before opening a new one.
        current = self.composer_text()
        if current == self.profile['skill_command']:
            # Resume only the exact bare skill command left by this workflow.
            # A final keystroke reopens Electron's asynchronously populated menu.
            self.focus_composer().type_keys('^{END}{BACKSPACE}' + self.profile['skill_command'][-1],
                                           pause=.15, set_foreground=False)
            time.sleep(.7)
            return False
        if current and current != self.profile['skill_name']:
            raise DesktopBlocked('当前输入框已有草稿，请先处理草稿；脚本不会覆盖')
        if current:
            self.verify_skill()
        self.click('new_task')
        time.sleep(.4)
        current = self.composer_text()
        if current == self.profile['skill_name']:
            self.verify_skill()
            return True
        if current:
            raise DesktopBlocked('新对话输入框不是空白，已暂停')
        self.focus_composer().type_keys(self.profile['skill_command'], with_spaces=True, pause=.08, set_foreground=False)
        time.sleep(.5)
        try:
            self.selected('skill_menu_option')
        except DesktopBlocked:
            if self.composer_text() != self.profile['skill_command']:
                raise
            self.focus_composer().type_keys('^{END}{BACKSPACE}' + self.profile['skill_command'][-1],
                                           pause=.15, set_foreground=False)
            time.sleep(.7)
        return False

    def verify_skill(self):
        composer = self.selected('composer')
        matches = [c for c in composer.descendants(control_type='Button')
                   if c.element_info.name == self.profile['skill_name']]
        if len(matches) != 1:
            raise DesktopBlocked('输入框内没有选中的 market-stall-demo 技能，禁止发送')

    def prepare_input(self, prompt, resume_skill=False):
        self.activate()
        self.exit_display()
        if resume_skill:
            self.verify_skill()
            if self.composer_text() != self.profile['skill_name']:
                raise DesktopBlocked('仅允许恢复只有技能按钮的空白草稿')
        else:
            already_selected = self.open_skill()
            if not already_selected:
                self.click('skill_menu_option')
                time.sleep(.3)
            self.verify_skill()
        self.paste(prompt)
        self.verify_input(prompt)

    def paste(self, prompt):
        import win32clipboard as clip
        import win32con
        import struct
        control = self.focus_composer()
        # Snapshot text, image DIB/PNG and file-drop formats before temporarily pasting.
        clip.OpenClipboard()
        try:
            old = []
            fmt = clip.EnumClipboardFormats(0)
            while fmt:
                if fmt == win32con.CF_BITMAP and (clip.IsClipboardFormatAvailable(win32con.CF_DIB) or clip.IsClipboardFormatAvailable(17)):
                    fmt = clip.EnumClipboardFormats(fmt)
                    continue  # Windows can regenerate the bitmap from the preserved DIB.
                value = clip.GetClipboardData(fmt)
                if fmt == win32con.CF_HDROP:
                    value = struct.pack('<IiiII', 20, 0, 0, 0, 1) + ('\0'.join(value) + '\0\0').encode('utf-16-le')
                if not isinstance(value, (str, bytes)):
                    raise DesktopBlocked('剪贴板含无法保留的特殊格式，未更改剪贴板或发送')
                old.append((fmt, value))
                fmt = clip.EnumClipboardFormats(fmt)
            clip.EmptyClipboard()
            clip.SetClipboardText(prompt, win32con.CF_UNICODETEXT)
        finally:
            clip.CloseClipboard()
        try:
            self.focus_composer().type_keys('^{END}', set_foreground=False)
            self.focus_composer().type_keys('^v', set_foreground=False)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.normalized(prompt) in self.composer_text():
                    return
                time.sleep(.2)
            raise DesktopBlocked('粘贴内容无法完整读回，未发送')
        finally:
            clip.OpenClipboard()
            try:
                if clip.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT) and clip.GetClipboardData(win32con.CF_UNICODETEXT) == prompt:
                    clip.EmptyClipboard()
                    for fmt, value in old:
                        clip.SetClipboardData(fmt, value)
            finally:
                clip.CloseClipboard()

    def verify_input(self, prompt):
        self.verify_skill()
        text = self.composer_text()
        expected = self.normalized(prompt)
        if not text.endswith(expected) or self.normalized(text[:-len(expected)]) != self.profile['skill_name']:
            raise DesktopBlocked('输入内容与本轮 prompt 不一致，禁止发送')

    def submit(self, prompt):
        self.activate()
        self.verify_input(prompt)
        self.click('send')

    def message_visible(self, task_id):
        # Exclude the editor subtree: a pasted draft is not evidence of submission.
        for control in self.controls():
            if control.element_info.control_type != 'Text' or task_id not in (control.element_info.name or ''):
                continue
            parent = control.parent()
            in_editor = False
            for _ in range(40):
                if parent is None or parent == self.window:
                    break
                if parent.element_info.control_type == 'Edit':
                    in_editor = True
                    break
                parent = parent.parent()
            if not in_editor:
                return True
        return False

    def confirm_submission(self, task_id):
        deadline = time.monotonic() + self.profile.get('send_confirmation_timeout_seconds', 20)
        while time.monotonic() < deadline:
            if self.message_visible(task_id):
                return True
            time.sleep(.5)
        return False


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, default=Path(__file__).with_name('desktop-profile.example.json'))
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding='utf-8-sig'))
    print(json.dumps(WorkBuddyDesktop(profile).inspect(), ensure_ascii=False, indent=2))
