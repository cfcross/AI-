"""Deterministic skill rendering and atomic delivery; no AI, printing or UI actions."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbuddy.protocol import validate_manifest
from workbuddy.run import read, save


def stage(job, name, message):
    now = datetime.now(timezone.utc).isoformat()
    save(job / 'progress.json', {'schema_version': 1, 'task_id': job.name,
         'stage': name, 'message': message, 'updated_at': now})
    # One writer (this CLI), append instead of overwriting stage history.
    with (job / 'render-events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at': now, 'stage': name}, ensure_ascii=False) + '\n')


def engine(skill, name):
    spec = importlib.util.spec_from_file_location('market_' + name, skill / 'assets' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def background(job, request, value):
    path = (job / value).resolve(strict=True)
    allowed = {Path(p).resolve() for p in request.get('photos', [])}
    if not path.is_relative_to(job) and path not in allowed:
        raise ValueError('底图必须在本任务内或属于本任务明确提供的照片')
    if not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
        raise ValueError('底图必须为真实图片文件')
    from PIL import Image
    with Image.open(path) as image:
        image.verify()
    return str(path)


def prepare_items(job, request, design):
    groups = [('poster', design.get('posters', [])), ('price_board', design.get('price_boards', []))]
    expected = {'poster', 'price_board'} if design.get('intent') == 'both' else {design.get('intent')}
    actual, prepared = set(), []
    for kind, items in groups:
        if not isinstance(items, list):
            raise ValueError('posters/price_boards 必须是数组')
        for index, original in enumerate(items):
            item = dict(original)
            if not isinstance(item.get('name'), str) or not item['name'].strip():
                raise ValueError('每张成品必须提供店名（可按规则代拟）')
            item['key'] = job.name + '_' + kind + '_' + str(index + 1)
            if kind == 'poster':
                source = item.get('photo') or item.get('bg')
                if not isinstance(source, str) or not source.strip():
                    raise ValueError('海报必须提供真实底图，不能静默回退纯色')
                item['bg'] = background(job, request, source)
                item.pop('photo', None)
                if not re.fullmatch(r'\d{1,3}% \d{1,3}%', item.get('pos', '50% 50%')):
                    raise ValueError('pos 必须是两个百分比')
                for key in ('name', 'cat', 'line', 'en', 'top_cn', 'top_en', 'side1', 'side2', 'no'):
                    item[key] = html.escape(str(item.get(key, ' ')), quote=True)
            else:
                if item.get('tpl', 't1') not in ('t1', 't2', 't3', 't4'):
                    raise ValueError('未知价格牌模板')
                products = [product for group in item.get('groups', []) for product in group.get('items', [])]
                if not products or any(not str(p.get(k, '')).strip() for p in products for k in ('n', 'p', 'u')):
                    raise ValueError('价格牌必须明确每项商品、金额、单位')
                if len(products) > 15:
                    raise ValueError('超过15项请拆成多个 A4 price_boards')
                item.setdefault('meta', ' ')
            actual.add(kind)
            prepared.append((kind, item))
    if actual != expected or not prepared:
        raise ValueError('intent 与最终物料清单不匹配')
    return prepared


def render(job, design_path):
    job = Path(job).resolve()
    if (job / 'result.json').exists():
        raise ValueError('任务已有交付结果，不覆盖或重复打印')
    request = read(job / 'request.json')
    design_path = (job / design_path).resolve()
    if not design_path.is_relative_to(job):
        raise ValueError('设计 JSON 必须在本任务目录')
    design = read(design_path)
    items = prepare_items(job, request, design)
    # Fresh filenames prevent an old PNG masquerading as a successful rerender
    # when the browser fails before writing a replacement.
    attempt = uuid.uuid4().hex[:8]
    for _, item in items:
        item['key'] += '_' + attempt
    skill = Path(request['skill_path']).resolve(strict=True)
    stage(job, 'rendering', '正在按固定模板排版最终成品')
    started = time.monotonic()
    files, engines = [], {}
    for kind, item in items:
        name = 'poster_a' if kind == 'poster' else 'price_board'
        if name not in engines:
            engines[name] = engine(skill, name)
        module = engines[name]
        if kind == 'poster':
            output = module.build_one(item, str(job / 'out'), module.find_chrome(), scale=2)
        else:
            output = module.build_one(item, item.get('tpl', 't1'), str(job / 'out'), module.find_chrome(), scale=2)
        if not output[1]:
            raise ValueError('渲染未输出 PNG')
        files.append({'kind': kind, 'path': Path(output[1]).relative_to(job).as_posix()})
    result = {'schema_version': 1, 'task_id': job.name, 'status': 'completed',
              'intent': design['intent'], 'summary': str(design.get('summary', '')),
              'message': '成品已生成并通过核图', 'questions': [], 'final_selection': True,
              'files': files, 'render_seconds': round(time.monotonic() - started, 3)}
    validate_manifest(job, request, result)
    save(job / 'rendered.json', {'result': result, 'sha256': {
        f['path']: hashlib.sha256((job / f['path']).read_bytes()).hexdigest() for f in files}})
    stage(job, 'verifying', '图片已排版，正在核对商品和文字')
    return {'preview': files, 'render_seconds': result['render_seconds'], 'next': '核图合格后调用 complete --approved'}


def complete(job, approved=False):
    job = Path(job).resolve()
    if not approved:
        raise ValueError('须实际核图后提供 --approved')
    request, pending = read(job / 'request.json'), read(job / 'rendered.json')
    result = validate_manifest(job, request, pending['result'])
    for item in result['files']:
        if hashlib.sha256((job / item['path']).read_bytes()).hexdigest() != pending['sha256'].get(item['path']):
            raise ValueError('核图文件已变化，请重新渲染并核对')
    target = job / 'result.json'
    if target.exists():
        if read(target) != result:
            raise ValueError('已存在其他交付结果，未覆盖')
        return result  # Idempotent; do not alter timestamp or trigger another print.
    save(target, result)
    stage(job, 'completed', '成品已核对完成，交由程序展示、响铃及打印')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['begin', 'render', 'complete'])
    parser.add_argument('--job', required=True, type=Path)
    parser.add_argument('--design', default='design.json')
    parser.add_argument('--approved', action='store_true')
    args = parser.parse_args()
    job = args.job.resolve()
    if args.command == 'begin':
        if read(job / 'request.json')['task_id'] != job.name:
            raise ValueError('任务编号不匹配')
        (job / 'gen').mkdir(exist_ok=True)
        stage(job, 'generating_background', '正在生成一张高质量底图')
        print('直接调用内置 ImageGen，quality=high，1张，1024x1536；不要生成候选合集。')
    else:
        value = render(job, args.design) if args.command == 'render' else complete(job, args.approved)
        print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
