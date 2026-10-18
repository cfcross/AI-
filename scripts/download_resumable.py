"""Bounded concurrent range downloader with retries and persistent parts."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.request

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    parts = args.output.with_name(args.output.name + '.parts')
    parts.mkdir(parents=True, exist_ok=True)
    metadata = parts / 'source.json'
    if metadata.exists() and json.loads(metadata.read_text())['url'] == args.url:
        total = json.loads(metadata.read_text())['size']
    else:
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(args.url, headers={'Range': 'bytes=0-0'}), timeout=30) as r:
                    if r.status != 206:
                        raise RuntimeError('Server must support byte ranges')
                    total = int(r.headers['Content-Range'].split('/')[-1])
                break
            except Exception:
                if attempt == 5: raise
                time.sleep(2 ** attempt)
    identity = {'url': args.url, 'size': total}
    if metadata.exists() and json.loads(metadata.read_text()) != identity:
        old = json.loads(metadata.read_text())
        if old['size'] != total or not args.sha256:
            raise RuntimeError('Resume source changed without a trusted content hash')
    metadata.write_text(json.dumps(identity))
    # Resolve the official CDN once, avoiding one API redirect per range.
    download_url = args.url
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(args.url, headers={'Range': 'bytes=0-0'}), timeout=30) as r:
                download_url = r.geturl()
            break
        except Exception:
            if attempt == 5: raise
            time.sleep(2 ** attempt)
    lock = threading.Lock()
    transferred = [0]
    finished = [0]
    size = 8 * 1024 * 1024
    def fetch(start):
        end = min(total, start + size) - 1
        path = parts / str(start)
        if path.exists() and path.stat().st_size == end - start + 1:
            with lock: finished[0] += end - start + 1
            return path
        for attempt in range(6):
            try:
                req = urllib.request.Request(download_url, headers={'Range': f'bytes={start}-{end}'})
                with urllib.request.urlopen(req, timeout=45) as r:
                    expected = f'bytes {start}-{end}/{total}'
                    if r.status != 206 or r.headers.get('Content-Range') != expected:
                        raise RuntimeError('Incorrect range response')
                    temp = path.with_suffix('.tmp')
                    with temp.open('wb') as out:
                        while chunk := r.read(256 * 1024):
                            out.write(chunk)
                            with lock: transferred[0] += len(chunk)
                    if temp.stat().st_size != end - start + 1:
                        raise RuntimeError('Incomplete range')
                    temp.replace(path)
                    with lock: finished[0] += end - start + 1
                    return path
            except Exception as error:
                if attempt == 5: raise
                print(f'retry offset={start}: {error}', flush=True)
                time.sleep(min(2 ** attempt, 15))
    print(json.dumps({'file': str(args.output), 'bytes': total, 'workers': args.workers}), flush=True)
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(fetch, n) for n in range(0, total, size)]
        previous, tick = 0, time.monotonic()
        pending = set(futures)
        while pending:
            done, pending = concurrent.futures.wait(pending, timeout=10, return_when=concurrent.futures.FIRST_EXCEPTION)
            for f in done: f.result()
            now = time.monotonic()
            with lock: received, complete = transferred[0], finished[0]
            print(json.dumps({'complete_mb': round(complete / 1e6, 1), 'total_mb': round(total / 1e6, 1),
                              'speed_MB_s': round((received - previous) / 1e6 / (now - tick), 2)}), flush=True)
            previous, tick = received, now
    digest = hashlib.sha256()
    temp = args.output.with_suffix(args.output.suffix + '.assembling')
    with temp.open('wb') as out:
        for future in futures:
            with future.result().open('rb') as part:
                while chunk := part.read(4 * 1024 * 1024):
                    out.write(chunk); digest.update(chunk)
    actual = digest.hexdigest()
    if args.sha256 and actual != args.sha256:
        raise RuntimeError(f'SHA256 mismatch: {actual}')
    temp.replace(args.output)
    print(json.dumps({'saved': str(args.output), 'sha256': actual}), flush=True)

if __name__ == '__main__':
    main()
