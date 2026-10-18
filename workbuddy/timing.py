"""Server-observed stage timings, independent of model-written clock strings."""
from datetime import datetime, timezone
from pathlib import Path
import time
from workbuddy.run import read, save


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def observe(job, request, state):
    if request.get('timing_schema_version') != 1:
        return None
    path = job / 'timings.json'
    old = read(path) if path.exists() else {}
    value = {**old, 'created_at': request['created_at']}
    if state.get('submitted_at'):
        value['submitted_at'] = state['submitted_at']
    progress = job / 'progress.json'
    if progress.exists():
        report = read(progress)
        if report.get('task_id') == job.name:
            events = list(value.get('stages', []))
            name = report.get('stage')
            if name and (not events or events[-1]['stage'] != name):
                events.append({'stage': name, 'at': iso(progress.stat().st_mtime)})
            value['stages'] = events
    images = list((job / 'gen').glob('*.png'))
    if images:
        times = [p.stat().st_mtime for p in images]
        value.update(background_count=len(images), first_background_at=iso(min(times)),
                     last_background_at=iso(max(times)))
    result = job / 'result.json'
    if result.exists():
        value['result_written_at'] = iso(result.stat().st_mtime)
    if value != old:
        save(path, value)
    start = datetime.fromisoformat(value['created_at']).timestamp()
    end = datetime.fromisoformat(value['result_written_at']).timestamp() if value.get('result_written_at') else time.time()
    return {**value, 'elapsed_seconds': round(max(0, end - start), 1)}
