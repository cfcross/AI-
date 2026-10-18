"""Application-owned WorkBuddy worker, validated gallery and durable print dispatch."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import quote

from workbuddy.protocol import create_task, validate_result
from workbuddy.run import read, save
from workbuddy.timing import observe as observe_timing


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class PrintDispatchError(RuntimeError):
    def __init__(self, message, detail):
        super().__init__(detail)
        self.user_message = message


class Pipeline:
    def __init__(self, root, config, config_path, printer_runner=None, ring_handler=None):
        self.root, self.config, self.config_path = Path(root), config, Path(config_path)
        self.data = (self.root / config['storage']['directory']).resolve()
        self.base = self.data / 'workbuddy'
        self.jobs = self.base / 'jobs'
        self.jobs.mkdir(parents=True, exist_ok=True)
        self.options = config.get('automation', {})
        self.print_options = config.get('printing', {})
        policy = self.base / 'automation-policy.json'
        if not policy.exists():
            save(policy, {'activated_at': utcnow(), 'note': 'Earlier tasks are displayed but never automatically printed.'})
        self.activated = read(policy)['activated_at']
        self.ring_handler = ring_handler
        self.ring_enabled = config.get('notifications', {}).get('enabled', False)
        ring_policy = self.base / 'notification-policy.json'
        if self.ring_enabled and not ring_policy.exists():
            save(ring_policy, {'activated_at': utcnow()})
        self.ring_activated = read(ring_policy)['activated_at'] if ring_policy.exists() else utcnow()
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.child = None
        self.cache = {}
        self.status = {'enabled': self.options.get('enabled', False), 'worker_alive': False,
                       'message': '正在启动物料处理', 'tasks': [], 'printer': self.print_options.get('printer_name', ''),
                       'auto_print': self.print_options.get('enabled', False)}
        self.status['print_mode'] = {'duplex': self.print_options.get('duplex', 'LongEdge'),
                                    'layout': self.print_options.get('layout', 'Cover'), 'back_content': 'same_image'}
        self.printer_runner = printer_runner or self.run_printer
        self.thread = threading.Thread(target=self.run, name='material-pipeline', daemon=True)

    def snapshot(self):
        with self.lock:
            value = json.loads(json.dumps(self.status))
        # scan may be blocked in a driver call. Read its durable ledger so the
        # page still shows "submitting" instead of the previous "not_printed".
        for task in value.get('tasks', []):
            try:
                task['printing'] = self.load_print(self.jobs / task['id'])
            except (OSError, ValueError):
                pass
        return value

    def notification_event(self, event):
        job = self.job_path(event['task_id'])
        if event.get('state') not in ('pending', 'dialing', 'ringing', 'answered', 'done', 'failed'):
            return
        with self.lock:
            previous = read(job / 'notification.json') if (job / 'notification.json').exists() else {}
            save(job / 'notification.json', {**previous, 'state': event['state'],
                 'message': event.get('message', ''), 'updated_at': utcnow()})

    def notify_completed(self, job, request):
        if not self.ring_enabled or not self.ring_handler or request['created_at'] < self.ring_activated:
            return None
        path = job / 'notification.json'
        with self.lock:
            notice = read(path) if path.exists() else {'state': 'pending', 'created_at': utcnow()}
            if notice['state'] != 'pending':
                return notice  # Never repeat an uncertain call after restart.
            if time.time() - datetime.fromisoformat(notice['created_at']).timestamp() > 300:
                notice.update(state='skipped', message='线路持续忙，已取消过期响铃提醒')
                save(path, notice)
                return notice
            if time.time() < notice.get('retry_after', 0):
                return notice
            notice.update(state='sending', message='正在请求电话提醒', retry_after=time.time()+5)
            save(path, notice)
        try:
            sent = self.ring_handler(job.name)
        except Exception as error:
            notice.update(state='failed', message='响铃请求失败：' + str(error))
            save(path, notice)
        else:
            if not sent:
                notice.update(state='pending', message='电话忙或尚未就绪，等待提醒')
                save(path, notice)
        return read(path)

    def start(self):
        self.thread.start()

    def start_sender(self):
        log_path = self.base / 'integrated-worker.log'
        with log_path.open('a', encoding='utf-8') as log:
            self.child = subprocess.Popen([sys.executable, '-u', str(self.root / 'workbuddy/run.py'),
                '--config', str(self.config_path), 'watch'], cwd=self.root,
                stdout=log, stderr=log, env={**os.environ, 'PYTHONUTF8': '1'},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def run(self):
        try:
            if self.options.get('enabled'):
                self.start_sender()
            while not self.closed.is_set():
                try:
                    self.scan()
                except Exception as error:
                    with self.lock:
                        self.status['message'] = '物料状态读取失败：' + str(error)
                self.closed.wait(1)
        except Exception as error:
            with self.lock:
                self.status['message'] = '物料监听启动失败：' + str(error)

    def stop(self):
        self.closed.set()
        if self.child and self.child.poll() is None:
            control = self.base / 'watcher.json'
            # The global sender lock guarantees only one worker. Request graceful stop.
            if control.exists():
                save(self.base / 'stop.json', {'id': read(control)['id']})
            try:
                self.child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.child.terminate()
        self.thread.join(timeout=5) if self.thread.is_alive() else None

    def result(self, job):
        path = job / 'result.json'
        if not path.exists():
            return None
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('结果文件过大')
        # Cache only while the manifest AND every referenced PNG are unchanged.
        raw = read(path)
        paths = []
        for item in raw.get('files', []):
            candidate = self.safe_image(job, item.get('path', ''))
            paths.append((str(candidate), candidate.stat().st_mtime_ns, candidate.stat().st_size))
        stamp = (path.stat().st_mtime_ns, path.stat().st_size, tuple(paths))
        cached = self.cache.get(str(job))
        if cached and cached[0] == stamp:
            return cached[1]
        value = validate_result(job)
        self.cache[str(job)] = (stamp, value)
        return value

    @staticmethod
    def safe_image(job, relative):
        path = (job / relative).resolve()
        if not path.is_relative_to((job / 'out').resolve()) or path.suffix.lower() != '.png' or not path.is_file():
            raise ValueError('图片必须是当前任务 out 目录中的 PNG')
        return path

    def image_path(self, task_id, relative):
        job = self.job_path(task_id)
        result = self.result(job)
        if not result or result['status'] != 'completed' or relative not in [f['path'] for f in result['files']]:
            raise ValueError('图片尚未通过交付校验')
        return self.safe_image(job, relative)

    def job_path(self, task_id):
        if str(uuid.UUID(task_id)) != task_id:
            raise ValueError('无效任务编号')
        job = (self.jobs / task_id).resolve()
        if not job.is_relative_to(self.jobs.resolve()) or not (job / 'request.json').is_file():
            raise ValueError('任务不存在')
        return job

    def create(self, text):
        if not self.options.get('enabled'):
            raise ValueError('WorkBuddy 自动处理未启用')
        skill = Path.home() / '.workbuddy/skills/market-stall-demo'
        job = create_task(text, self.jobs, source_id='web:' + str(uuid.uuid4()),
                          skill_path=str(skill), python_path=sys.executable, generation_config=self.config)
        return job.name

    def retry_desktop(self, task_id):
        job = self.job_path(task_id)
        with self.lock:
            state = read(job / 'state.json')
            if state['state'] != 'waiting_desktop' or state.get('submitted'):
                raise ValueError('仅允许重试尚未发送的桌面任务')
            save(job / 'state.json', {**state, 'automatic_retry': True, 'message': '已请求重试，请保持 WorkBuddy 输入框空白'})

    def request_print(self, task_id, selected):
        job = self.job_path(task_id)
        result = self.result(job)
        if not result or result['status'] != 'completed':
            raise ValueError('任务未完成，不能打印')
        valid = {f['path'] for f in result['files']}
        if not isinstance(selected, list) or not selected or any(not isinstance(p, str) or p not in valid for p in selected):
            raise ValueError('请选择本任务已完成的图片')
        if len(set(selected)) != len(selected):
            raise ValueError('不能重复选择同一图片')
        with self.lock:
            ledger = self.load_print(job)
            if ledger['state'] in ('submitting', 'spooled', 'submission_unknown'):
                raise ValueError('该任务已提交或提交结果待核对，禁止重复打印')
            save(job / 'print-request.json', {'files': selected, 'time': utcnow()})

    def load_print(self, job):
        path = job / 'print.json'
        if path.exists():
            value = read(path)
            # Older versions placed the full subprocess command in the UI.
            if value.get('state') == 'submission_unknown' and 'Command ' in value.get('message', ''):
                value['message'] = '打印调用超时，结果待核对。请检查打印机与 Windows 打印后台服务；本任务未自动重印。'
            return value
        # Respect print receipts from the earlier manual test tools.
        for old in job.glob('print-manual*.json'):
            value = read(old)
            if value.get('status') in ('spooled', 'submitting', 'submission_unknown'):
                return {'state': value['status'], 'message': '此任务已由手动打印工具提交，不再自动打印', 'items': []}
        return {'state': 'not_printed', 'items': [], 'message': ''}

    def print_task(self, job, result, selected=None):
        with self.lock:
            old = self.load_print(job)
            if old['state'] in ('spooled', 'submission_unknown', 'submitting'):
                return old
            files = result['files']
            if selected is not None:
                if not selected or any(p not in {f['path'] for f in files} for p in selected):
                    raise ValueError('选择的图片已变化，请刷新后重新选择')
                files = [f for f in files if f['path'] in selected]
            elif len(files) != len({f['kind'] for f in files}) and not (
                self.config.get('generation', {}).get('auto_print_final_pages') is True
                and read(job / 'request.json').get('generation_policy', {}).get('auto_print_final_pages') is True
                and result.get('final_selection') is True
            ):
                value = {'state': 'selection_needed', 'items': [], 'message': '有多个版本或分页，请在网页选择要打印的图片'}
                save(job / 'print.json', value)
                return value
            printer = self.print_options.get('printer_name', '').strip()
            duplex = self.print_options.get('duplex', 'LongEdge')
            layout = self.print_options.get('layout', 'Cover')
            if duplex not in ('LongEdge', 'ShortEdge', 'Simplex') or layout not in ('Cover', 'Contain'):
                raise ValueError('打印模式无效，请检查 duplex / layout 配置')
            if not printer:
                value = {'state': 'failed', 'items': [], 'message': '请在 config.json 的 printing.printer_name 配置实体打印机'}
                save(job / 'print.json', value)
                return value
            items = []
            for item in files:
                image = self.safe_image(job, item['path'])
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
                items.append({'path': item['path'], 'sha256': digest,
                              'receipt': 'print-receipts/' + digest + '.json'})
            ledger = {'state': 'submitting', 'printer': printer, 'paper': 'A4', 'color': True,
                      'duplex': duplex, 'layout': layout, 'back_content': 'same_image',
                      'pages_per_image': 1 if duplex == 'Simplex' else 2,
                      'items': items, 'started_at': utcnow(), 'message': '正在提交 A4 彩色打印'}
            save(job / 'print.json', ledger)
        # Do not hold the snapshot lock while a printer driver is blocking.
        try:
            for item in items:
                receipt = job / item['receipt']
                if receipt.exists():
                    previous = read(receipt)
                    if previous.get('status') == 'spooled':
                        continue
                    raise RuntimeError('已有打印提交记录，需核对队列，未重印')
                self.printer_runner(job / item['path'], printer, receipt, 'AI-' + job.name[:8])
                if not receipt.exists() or read(receipt).get('status') != 'spooled':
                    raise RuntimeError('未取得 Windows 打印提交回执')
            sides = '单面' if duplex == 'Simplex' else '双面同图'
            fit = '铺满可打印区' if layout == 'Cover' else '完整图片适配'
            ledger.update(state='spooled', message=f'已提交打印（A4 彩色、{sides}、{fit}）；硬件边缘无法印刷，实际出纸请查看打印机')
        except Exception as error:
            # Once dispatch begins, never automatically retry even if the process died.
            ledger.update(state='submission_unknown', message=getattr(error, 'user_message',
                '打印未确认，请核对打印机与队列；详细原因已保存到本地打印记录。'),
                error_detail=str(error))
        ledger['updated_at'] = utcnow()
        save(job / 'print.json', ledger)
        return ledger

    def run_printer(self, image, printer, receipt, name):
        shell = shutil.which('powershell.exe')
        if not shell:
            raise RuntimeError('未找到 Windows PowerShell')
        command = [shell, '-NoProfile', '-NonInteractive', '-File',
            str(self.root / 'scripts/print-image.ps1'), '-ImagePath', str(image),
            '-PrinterName', printer, '-ReceiptPath', str(receipt), '-DocumentName', name,
            '-DuplexMode', self.print_options.get('duplex', 'LongEdge'),
            '-Layout', self.print_options.get('layout', 'Cover'),
            '-OffsetXmm', str(self.print_options.get('offset_x_mm', 0)),
            '-OffsetYmm', str(self.print_options.get('offset_y_mm', 0)),
            '-BackOffsetXmm', str(self.print_options.get('back_offset_x_mm', 0)),
            '-BackOffsetYmm', str(self.print_options.get('back_offset_y_mm', 0))]
        try:
            done = subprocess.run(command, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired as error:
            try:
                status = read(receipt)
            except (OSError, ValueError):
                status = {}
            preparing = status.get('status') == 'preparing'
            message = ('打印驱动准备超时，尚未调用打印。请检查打印机，必要时由管理员重启 Windows 打印后台服务。'
                       if preparing else '打印调用超时，提交结果待核对；本任务未自动重印。')
            raise PrintDispatchError(message, f'90s timeout; stage={status.get("stage", "unknown")}; {error}') from error
        if done.returncode:
            raise RuntimeError(done.stderr.decode('utf-8', errors='replace')[-500:])

    def scan(self):
        tasks = []
        to_print = []
        for path in sorted(self.jobs.glob('*/request.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            job = path.parent
            try:
                request, state = read(path), read(job / 'state.json')
                task = {'id': job.name, 'created_at': request['created_at'], 'text': request['transcript'],
                        'source_id': request.get('source_id'),
                        'state': state['state'], 'message': state.get('message', ''), 'files': [], 'questions': [],
                        'automatic_print_eligible': request['created_at'] >= self.activated}
                try:
                    timing = observe_timing(job, request, state)
                    if timing:
                        task['timing'] = timing
                except (OSError, ValueError, TypeError):
                    pass  # Diagnostics must never prevent display/ringing/printing.
                progress = job / 'progress.json'
                if progress.is_file() and progress.stat().st_size < 32768:
                    try:
                        report = read(progress)
                        if report.get('task_id') == job.name:
                            task['progress'] = {k: report[k] for k in ('stage', 'message', 'updated_at') if k in report}
                    except (OSError, ValueError):
                        pass
                try:
                    result = self.result(job)
                except (OSError, ValueError, TypeError, KeyError) as error:
                    result = None
                    task['validation_error'] = str(error)
                if result:
                    task.update(state=result['status'], message=result.get('message', ''),
                                questions=result.get('questions', []), summary=result.get('summary', ''))
                    for item in result.get('files', []):
                        task['files'].append({**item, 'url': '/api/jobs/' + job.name + '/image?path=' + quote(item['path'])})
                ledger = self.load_print(job)
                task['printing'] = ledger
                print_request = job / 'print-request.json'
                if result and result['status'] == 'completed':
                    notice = self.notify_completed(job, request)
                    if notice:
                        task['notification'] = notice
                    manual = read(print_request)['files'] if print_request.exists() else None
                    automatic = self.print_options.get('enabled') and task['automatic_print_eligible']
                    if manual is not None or (automatic and ledger['state'] == 'not_printed'):
                        to_print.append((job, result, manual))
                tasks.append(task)
            except (OSError, ValueError, KeyError, TypeError):
                continue  # Atomic updates or incomplete task creation; inspect again next tick.
        alive = bool(self.child and self.child.poll() is None)
        with self.lock:
            self.status.update(tasks=tasks[:30], worker_alive=alive,
                message='电话识别后自动生成物料' if alive else 'WorkBuddy 监听未运行，请查看 data/workbuddy/integrated-worker.log')
        for job, result, selected in reversed(to_print):
            if self.closed.is_set():
                break
            self.print_task(job, result, selected)
            (job / 'print-request.json').unlink(missing_ok=True)
