"""Standard-library-only frontend fixture server. No hardware or agent imports."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
STAGES = ['recording', 'transcribing', 'understanding', 'generating_background', 'rendering', 'verifying', 'completed', 'needs_input', 'failed']

def snapshot(stage):
    now = datetime.now(timezone.utc).isoformat()
    text = '演示鱼档，鲈鱼每斤35元，草鱼每斤12元，做一张价格牌。'
    completed = stage == 'completed'
    phase = stage if stage in ('recording', 'transcribing') else 'ready'
    task = {'id': 'demo-task', 'text': text, 'created_at': now,
            'state': stage if stage in ('completed', 'needs_input', 'failed') else 'submitted',
            'progress': {'stage': stage, 'message': '前端模拟数据'},
            'questions': ['请补充商品计价单位。'] if stage == 'needs_input' else [],
            'files': [{'kind': 'price_board', 'path': 'out/demo.svg', 'url': '/demo-image.svg'}] if completed else [],
            'printing': {'state': 'not_printed', 'message': '演示模式，不实际打印'},
            'notification': {'message': '演示模式，不实际响铃'}}
    return {'application_id': 'ai-printer-frontend-demo', 'phase': phase,
            'message': '前端演示模式 · 无电话、模型或打印操作', 'seconds': 6, 'packets': 300,
            'bridge_alive': False, 'phone_address': '演示设备', 'model': {'model': '模拟识别', 'device': 'none', 'dtype': 'none'},
            'history': [{'id': 'demo-record', 'text': text, 'time': now, 'seconds': 6, 'model': {'model': '模拟识别'}}],
            'pipeline': {'enabled': True, 'worker_alive': True, 'auto_print': False,
                         'print_mode': {'duplex': 'LongEdge'}, 'tasks': [task]}}

def make_handler(stage):
    started = time.monotonic()
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body, kind):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/api/state':
                active = stage if stage != 'cycle' else STAGES[min(int((time.monotonic()-started)/5) % 10, 6)]
                self.reply(200, json.dumps(snapshot(active), ensure_ascii=False).encode(), 'application/json; charset=utf-8')
            elif parsed.path in ('/', '/showcase'):
                page = 'index.html' if parsed.path == '/' else 'showcase.html'
                html = (ROOT / 'web' / page).read_text(encoding='utf-8').replace('__APP_TOKEN__', 'demo')
                html = html.replace('<body>', '<body><div style="background:#ffd966;color:#222;text-align:center;padding:6px">前端演示 · 不连接电话、WorkBuddy 或打印机</div>')
                self.reply(200, html.encode(), 'text/html; charset=utf-8')
            elif parsed.path == '/demo-image.svg':
                svg = '<svg xmlns="http://www.w3.org/2000/svg" width="840" height="1188"><rect width="840" height="1188" fill="#143e35"/><g fill="#fff8dd" text-anchor="middle" font-family="sans-serif"><text x="420" y="220" font-size="70">演示鱼档</text><text x="420" y="450" font-size="48">鲈鱼　35 元 / 斤</text><text x="420" y="590" font-size="48">草鱼　12 元 / 斤</text><text x="420" y="1000" font-size="25">前端示例 · 非实际生成结果</text></g></svg>'
                self.reply(200, svg.encode(), 'image/svg+xml; charset=utf-8')
            else:
                self.send_error(404)
        def do_POST(self):
            self.reply(409, json.dumps({'error': '演示模式禁止提交、生成、打印和停止真实服务'}, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        def log_message(self, *_):
            pass
    return Handler

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--stage', choices=['cycle', *STAGES], default='completed')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(args.stage))
    print(f'DEMO: http://127.0.0.1:{args.port}/showcase', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
