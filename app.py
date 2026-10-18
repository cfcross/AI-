"""Run: python app.py. Browser display + Python ASR + HT802 SIP bridge."""
import argparse
import copy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import urlsplit, parse_qs
from market_language import analyze

ROOT = Path(__file__).resolve().parent


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    for key in ('sip_port', 'device_sip_port', 'rtp_port'):
        if not 1024 <= config['phone'][key] <= 65535:
            raise ValueError('电话端口必须为 1024–65535')
    if not 1 <= config['phone']['max_seconds'] <= 300:
        raise ValueError('max_seconds 必须在 1–300 秒之间')
    if not 1024 <= config['display']['port'] <= 65535:
        raise ValueError('页面端口必须为 1024–65535')
    return config


class Application:
    def __init__(self, config, config_path, engine_factory=None):
        self.config, self.config_path = config, Path(config_path).resolve()
        self.root = self.config_path.parent
        self.data = (self.root / config['storage']['directory']).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.command_lock = threading.Lock()
        self.jobs = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.process = None
        self.engine = None
        self.engine_factory = engine_factory
        self.seen = set()
        self.token = secrets.token_urlsafe(32)
        self.state = {'application_id': 'ai-printer-phone-asr', 'phase': 'loading', 'message': '正在加载语音模型，请稍候', 'seconds': 0,
                      'packets': 0, 'model': {}, 'history': [], 'last_heartbeat': None,
                      'phone_address': config['phone']['device_address'], 'started': datetime.now().astimezone().isoformat()}
        self.worker = threading.Thread(target=self.initialize, daemon=True)
        self.pipeline = None
        if config.get('automation', {}).get('enabled'):
            from workbuddy.pipeline import Pipeline
            self.pipeline = Pipeline(self.root, config, self.config_path, ring_handler=self.notify_completion)
        # Restore the recent transcript view after service restarts.
        for path in sorted((self.data / 'transcripts').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                self.state['history'].append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass

    def set_state(self, **values):
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        if result['last_heartbeat']:
            result['bridge_alive'] = time.time() - result['last_heartbeat'] < 5
        else:
            result['bridge_alive'] = False
        result['pipeline'] = self.pipeline.snapshot() if self.pipeline else {'enabled': False, 'tasks': []}
        return result

    def command(self, command, **kwargs):
        process = self.process
        if process and process.poll() is None:
            try:
                with self.command_lock:
                    process.stdin.write(json.dumps({'command': command, **kwargs}) + '\n')
                    process.stdin.flush()
                return True
            except (OSError, ValueError):
                if not self.closed.is_set():
                    self.set_state(phase='error', message='电话进程失去连接，请重启程序')
        return False

    def notify_completion(self, task_id):
        state = self.snapshot()
        if state['phase'] != 'ready' or not state['bridge_alive']:
            return False
        return self.command('notify', task_id=task_id)

    def initialize(self):
        try:
            from asr_engine import AsrEngine
            node_setting = self.config['runtime']['node']
            node = shutil.which(str(self.root / node_setting) if '/' in node_setting or '\\' in node_setting else node_setting)
            if not node:
                raise RuntimeError('未找到 Node.js。请安装 Node.js 20 或更新版本。')
            asr_options = {**self.config['asr'], 'store_names': self.config.get('language_correction', {}).get('store_names', [])}
            self.engine = (self.engine_factory or AsrEngine)(asr_options, self.root)
            self.set_state(model=self.engine.info, message='模型已就绪，正在启动电话监听')
            if self.closed.is_set():
                return
            log = (self.data / 'sip-stderr.log').open('a', encoding='utf-8')
            try:
                self.process = subprocess.Popen([node, str(ROOT / 'sip/bridge.mjs'), str(self.config_path)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, cwd=self.root)
            finally:
                log.close()
            threading.Thread(target=self.read_events, daemon=True).start()
            while not self.closed.is_set():
                try:
                    event = self.jobs.get(timeout=.5)
                except queue.Empty:
                    continue
                self.process_recording(event)
                self.jobs.task_done()
        except Exception as error:
            self.set_state(phase='error', message=str(error))
            print('启动失败：', error, flush=True)

    def read_events(self):
        for line in self.process.stdout:
            try:
                event = json.loads(line)
                self.on_event(event)
                if event['event'] != 'heartbeat':
                    with (self.data / 'events.jsonl').open('a', encoding='utf-8') as log:
                        log.write(json.dumps(event, ensure_ascii=False) + '\n')
            except Exception as error:
                self.set_state(phase='error', message=str(error))
        if not self.closed.is_set():
            if self.snapshot()['phase'] != 'error':
                self.set_state(phase='error', message='电话服务已退出，请检查 data/sip-stderr.log 并重启')

    def on_event(self, event):
        kind = event['event']
        if kind == 'notification':
            if self.pipeline:
                self.pipeline.notification_event(event)
            return
        if kind == 'heartbeat':
            self.set_state(last_heartbeat=time.time())
            if self.snapshot()['phase'] == 'recording':
                self.set_state(seconds=round(event['seconds'], 1), packets=event['packets'])
        elif kind == 'listening':
            self.set_state(phase='ready', message='请拿起电话，听到提示音后开始说话')
            self.command('ready', value=True)
        elif kind in ('pickup', 'recording'):
            self.set_state(phase='recording', message='正在接收电话音频，说完请放下听筒', seconds=0, packets=0)
        elif kind == 'hangup':
            if event['id'] in self.seen:
                return
            self.seen.add(event['id'])
            if len(self.seen) > 1000:
                self.seen = {event['id']}
            event = dict(event, time=datetime.now().astimezone().isoformat())
            if event['reason'] != 'hangup':
                self.finish_record(event, error='通话异常结束（' + event['reason'] + '），未进行识别；请重新摘机')
                return
            self.set_state(phase='transcribing', message='已挂机，正在识别本轮语音', seconds=event['seconds'], packets=event.get('packets', 0))
            self.jobs.put_nowait(event)
        elif kind == 'call_ended':
            self.set_state(phase='ready', message='本轮未接通，请放下电话后重试')
            self.command('ready', value=True)
        elif kind in ('error', 'fatal'):
            self.set_state(phase='error', message=event['error'])

    def process_recording(self, event):
        try:
            result = self.engine.transcribe(event['file'])
            self.finish_record(event, **result)
        except Exception as error:
            self.finish_record(event, error=str(error))

    def finish_record(self, event, **result):
        record = {**event, **result, 'model': self.snapshot()['model'], 'text': result.get('text', '')}
        if record['text'] and not result.get('error'):
            options = self.config.get('language_correction', {})
            record['asr_text'] = record['text']
            record['correction'] = analyze(record['text'], options.get('store_names', []), options.get('enabled', True))
            record['text'] = record['correction']['text']
        audio_path = Path(record['file'])
        record['file'] = str(audio_path.relative_to(self.data))
        target = self.data / 'transcripts'
        target.mkdir(exist_ok=True)
        destination = target / (record['id'] + '.json')
        pending = destination.with_suffix('.json.tmp')
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(destination)  # Watcher can only see a complete corrected record.
        (target / (record['id'] + '.txt')).write_text(record['text'], encoding='utf-8')
        if not self.config['storage']['keep_audio']:
            audio_path.unlink(missing_ok=True)
        with self.lock:
            self.state['history'] = [record, *self.state['history']][:20]
            self.state['phase'] = 'ready'
            self.state['message'] = ('本轮失败：' + result['error']) if result.get('error') else ('未识别到有效语音，请重试' if not record['text'] else '识别完成，可以再次拿起电话')
        print(json.dumps({'transcript': record['text'], 'error': record.get('error'), 'id': record['id']}, ensure_ascii=False), flush=True)
        self.command('ready', value=True)

    def close(self):
        self.closed.set()
        if self.pipeline:
            self.pipeline.stop()
        self.command('stop')
        if self.process:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            token = self.headers.get('X-App-Token', '') or self.headers.get('Authorization', '').removeprefix('Bearer ')
            if not secrets.compare_digest(token, app.token):
                self.send_error(403)
                return
            if self.path != '/api/stop':
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    if not 0 < length <= 65536 or not app.pipeline:
                        raise ValueError('请求无效或未启用物料功能')
                    body = json.loads(self.rfile.read(length))
                    parts = self.path.strip('/').split('/')
                    if self.path == '/api/tasks':
                        output = {'task_id': app.pipeline.create(body.get('text', ''))}
                    elif len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'print':
                        app.pipeline.request_print(parts[2], body.get('files'))
                        output = {'ok': True}
                    elif len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'retry':
                        app.pipeline.retry_desktop(parts[2])
                        output = {'ok': True}
                    else:
                        raise ValueError('未知操作')
                    data = json.dumps(output, ensure_ascii=False).encode('utf-8')
                    self.send_response(200)
                except (ValueError, KeyError, TypeError, OSError) as error:
                    data = json.dumps({'error': str(error)}, ensure_ascii=False).encode('utf-8')
                    self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path in ('/api/state', '/health'):
                data = json.dumps(app.snapshot(), ensure_ascii=False).encode('utf-8')
                kind = 'application/json; charset=utf-8'
            elif parsed.path in ('/', '/showcase'):
                page = 'showcase.html' if parsed.path == '/showcase' else 'index.html'
                data = (ROOT / 'web' / page).read_text(encoding='utf-8').replace('__APP_TOKEN__', app.token).encode('utf-8')
                kind = 'text/html; charset=utf-8'
            elif parsed.path.startswith('/api/jobs/') and parsed.path.endswith('/image') and app.pipeline:
                try:
                    task_id = parsed.path.split('/')[3]
                    relative = parse_qs(parsed.query).get('path', [''])[0]
                    data = app.pipeline.image_path(task_id, relative).read_bytes()
                    kind = 'image/png'
                except (OSError, ValueError, KeyError, TypeError):
                    self.send_error(404)
                    return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass
    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, default=ROOT / 'config.json')
    p.add_argument('--no-browser', action='store_true')
    p.add_argument('--transcribe', type=Path, help='Offline WAV smoke test without phone')
    args = p.parse_args()
    config = load_config(args.config)
    if args.transcribe:
        from asr_engine import AsrEngine
        engine = AsrEngine(config['asr'], args.config.resolve().parent)
        print(json.dumps({'environment': engine.info, **engine.transcribe(args.transcribe)}, ensure_ascii=False, indent=2))
        return
    app = Application(config, args.config)
    try:
        server = ThreadingHTTPServer(('127.0.0.1', config['display']['port']), make_handler(app))
    except OSError as error:
        raise SystemExit('页面端口被占用，程序可能已运行：' + str(error))
    control_file = app.data / 'service.json'
    control_file.write_text(json.dumps({'port': config['display']['port'], 'token': app.token}), encoding='utf-8')
    app.worker.start()
    if app.pipeline:
        app.pipeline.start()
    url = f"http://127.0.0.1:{config['display']['port']}"
    print('文字展示页面：', url, '\n关闭程序请按 Ctrl+C。', flush=True)
    if config['display']['open_browser'] and not args.no_browser:
        webbrowser.open(url)
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
        control_file.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
