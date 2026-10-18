"""Send a prepared/ASR task through WorkBuddy UI, or watch new phone transcripts."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbuddy.protocol import create_task, validate_result
from workbuddy.desktop import WorkBuddyDesktop, DesktopBlocked

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


@contextmanager
def single_sender(data):
    import msvcrt
    path = data / 'workbuddy/sender.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('已有 WorkBuddy 发送脚本运行，请先关闭它，避免重复输入') from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def transition(job, state, **values):
    path = job / 'state.json'
    old = read(path)
    value = {**old, **values, 'state': state, 'updated_at': datetime.now(timezone.utc).isoformat()}
    save(path, value)
    print(json.dumps({'task_id': value['task_id'], 'state': state, 'message': value.get('message', '')}, ensure_ascii=False), flush=True)
    return value


def send_job(job, profile, *, resume_skill=False, adapter=None):
    job = Path(job).resolve()
    state = read(job / 'state.json')
    if state['state'] == 'sending':
        return transition(job, 'submission_unknown', message='上次发送过程中退出；请核对 WorkBuddy，不自动重发')
    if state['state'] not in ('prepared', 'waiting_desktop'):
        print('任务已处理或需要核对，不会重复发送：', state['state'], flush=True)
        return state
    request = read(job / 'request.json')
    # WorkBuddy turns long pasted text into a collapsed attachment. A short message
    # retains the actual utterance and points the selected skill to the full contract.
    prompt = build_message(job, request)
    (job / 'message.txt').write_text(prompt, encoding='utf-8')
    adapter = adapter or WorkBuddyDesktop(profile)
    try:
        adapter.prepare_input(prompt, resume_skill=resume_skill)
    except Exception as error:
        return transition(job, 'waiting_desktop', submitted=False, message=str(error),
                          automatic_retry=getattr(error, 'retryable', False))
    # Persist uncertainty BEFORE interacting with send. Any crash from here must not retry.
    transition(job, 'sending', submitted=False, message='正在提交，请勿操作 WorkBuddy 输入框')
    try:
        adapter.submit(prompt)
        if not adapter.confirm_submission(request['task_id']):
            raise DesktopBlocked('发送后未找到本轮任务消息')
    except Exception as error:
        return transition(job, 'submission_unknown', message=str(error) + '；可能已发送，禁止自动重发')
    result = transition(job, 'submitted', submitted=True, message='已在 WorkBuddy 对话中确认任务编号，等待处理结果')
    try:
        if profile.get('show_display_after_send') and hasattr(adapter, 'show_display'):
            adapter.show_display(profile['display_url'])
        elif hasattr(adapter, 'release_foreground'):
            adapter.release_foreground()
    except Exception as error:
        # A focus-restoration failure must never turn a confirmed send into a retry.
        print('任务已发送，但展示窗口切换失败：' + str(error), flush=True)
    return result


def build_message(job, request):
    transcript = request['transcript']
    # Long utterances stay in request.json; no silent truncation of business data.
    spoken = json.dumps(transcript, ensure_ascii=False) if len(transcript) <= 500 else '完整原文位于上述任务文件，较长未在此重复'
    return (f"任务编号：{request['task_id']}\n"
            f"请先读取本机任务说明文件：{job / 'prompt.txt'}\n"
            f"座机识别原文：{spoken}\n"
            "请使用已选中的 market-stall-demo 技能，判断用户要价格牌、海报还是两者；按任务文件的规则实际调用技能生成图片。"
            "店名缺失按技能代拟，没照片可生成一张底图；价格和单位不猜。"
            "按任务文件写 progress.json 更新阶段，完成后写 result.json。图片由本地网页展示和打印，请勿自动打开图片预览、切换浏览器或自行打印。")


class DisplayGuard:
    """Run in the sender thread, outside input/send; also catch late artifact opens."""
    def __init__(self, data, profile, adapter=None):
        self.data, self.profile, self.adapter = data, profile, adapter
        self.next_check = 0

    def tick(self):
        if not self.profile.get('show_display_after_send') or time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic() + 2
        try:
            task_ids = {p.parent.name for p in (self.data / 'workbuddy/jobs').glob('*/state.json')}
            self.adapter = self.adapter or WorkBuddyDesktop(self.profile)
            if self.adapter.restore_generated_preview(self.profile['display_url'], task_ids):
                print('图片预览已自动切回电话物料展示网页', flush=True)
        except Exception as error:
            # Presentation failure must never affect submission, ASR or printing.
            self.next_check = time.monotonic() + 15
            print('展示页恢复暂未成功，稍后重试：' + str(error), flush=True)


def monitor(job, timeout, check_stop=lambda: None):
    started = time.monotonic()
    last_error = ''
    while time.monotonic() - started < timeout:
        check_stop()
        if (job / 'result.json').exists():
            try:
                result = validate_result(job)
            except (OSError, ValueError) as error:
                last_error = str(error)
            else:
                return transition(job, result['status'], result=result,
                    message=result.get('message') or '；'.join(result.get('questions', [])) or '结果已通过文件校验')
        time.sleep(1)
    return transition(job, 'timed_out', message='尚未获得有效 result.json；WorkBuddy 可能仍在运行，不自动重发。' + last_error)


def prepare_record(path, root, data):
    record = read(path)
    if record.get('error') or record.get('reason') != 'hangup' or not record.get('text', '').strip():
        return None
    skill = Path.home() / '.workbuddy/skills/market-stall-demo'
    return create_task(record['text'], data / 'workbuddy/jobs', source_id=record['id'],
        skill_path=str(skill) if skill.is_dir() else '', python_path=sys.executable)


def watch(root, data, profile):
    identity = str(uuid.uuid4())
    control = data / 'workbuddy/watcher.json'
    stop_file = data / 'workbuddy/stop.json'
    save(control, {'id': identity, 'pid': os.getpid()})
    display_guard = DisplayGuard(data, profile)
    def check_stop():
        if stop_file.exists():
            try:
                if read(stop_file).get('id') == identity:
                    raise KeyboardInterrupt
            except (ValueError, OSError):
                pass
        display_guard.tick()
    try:
        watch_loop(root, data, profile, check_stop)
    finally:
        control.unlink(missing_ok=True)


def watch_loop(root, data, profile, check_stop):
    cursor_path = data / 'workbuddy/watch.json'
    transcript_dir = data / 'transcripts'
    if cursor_path.exists():
        seen = set(read(cursor_path)['seen'])
    else:
        # Explicit latest/send commands handle old records; first watch only sees new calls.
        seen = {path.stem for path in transcript_dir.glob('*.json')}
        save(cursor_path, {'seen': sorted(seen)})
    print('正在监听新识别记录。Ctrl+C 停止。首次启动不重发历史记录。', flush=True)
    next_attempt = {}
    last_notice = None
    while True:
        check_stop()
        pending = []
        blocked = []
        for path in (data / 'workbuddy/jobs').glob('*/state.json'):
            state = read(path)
            if state['state'] == 'sending':
                state = transition(path.parent, 'submission_unknown', message='上次发送过程中退出；需核对对话，不自动重发')
            if state['state'] in ('submission_unknown', 'timed_out'):
                # A late valid result can resolve uncertainty without another click.
                try:
                    result = validate_result(path.parent)
                except (OSError, ValueError):
                    blocked.append(path.parent)
                else:
                    transition(path.parent, result['status'], submitted=True, result=result,
                               message=result.get('message', '迟到结果已校验'))
            elif state['state'] == 'waiting_desktop':
                # Recognize the previous release's focus-only failure when upgrading.
                retry = state.get('automatic_retry', state.get('message') == 'WorkBuddy 未获得前台焦点，暂停输入')
                (pending if retry else blocked).append(path.parent)
            elif state['state'] in ('prepared', 'submitted'):
                pending.append(path.parent)
        for path in sorted(transcript_dir.glob('*.json'), key=lambda p: p.stat().st_mtime):
            if path.stem in seen:
                continue
            if len(pending) >= 20:
                break  # Leave unprocessed transcript on disk for the next queue pass.
            try:
                job = prepare_record(path, root, data)
            except (json.JSONDecodeError, PermissionError):
                continue  # ASR may still be finishing its write.
            if job and job not in pending:
                pending.append(job)
            seen.add(path.stem)
            save(cursor_path, {'seen': sorted(seen)})
        phase = 'needs_attention' if blocked else 'working' if pending else 'listening'
        notice = (phase, str(blocked[0]) if blocked else '')
        save(data / 'workbuddy/watch-status.json', {
            'phase': phase, 'blocked_job': notice[1], 'pending_count': len(pending),
            'updated_at': datetime.now(timezone.utc).isoformat()})
        if notice != last_notice:
            print('监听状态：' + phase + (' / ' + notice[1] if notice[1] else ''), flush=True)
            last_notice = notice
        if blocked:
            # Keep collecting ASR records, but do not click again after uncertainty
            # or overwrite a user draft. The watcher remains alive and stoppable.
            time.sleep(1)
            continue
        if pending:
            job = sorted(pending, key=lambda p: (p / 'request.json').stat().st_mtime)[0]
            if time.monotonic() < next_attempt.get(job, 0):
                time.sleep(1)
                continue
            state = send_job(job, profile)
            if state['state'] != 'submitted':
                next_attempt[job] = time.monotonic() + profile.get('desktop_retry_seconds', 15)
                continue
            monitor(job, profile.get('generation_timeout_seconds', 600), check_stop)
        else:
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--profile', type=Path, default=Path(__file__).with_name('desktop-profile.json'))
    sub = parser.add_subparsers(dest='command', required=True)
    send = sub.add_parser('send')
    send.add_argument('job', type=Path)
    send.add_argument('--resume-skill', action='store_true', help='仅恢复已选择指定技能且没有业务文字的草稿')
    send.add_argument('--wait', type=int, default=0)
    latest = sub.add_parser('latest')
    latest.add_argument('--wait', type=int, default=0)
    sub.add_parser('watch')
    sub.add_parser('stop')
    observe = sub.add_parser('result')
    observe.add_argument('job', type=Path)
    observe.add_argument('--wait', type=int, default=60)
    args = parser.parse_args()
    root = args.config.resolve().parent
    config = read(args.config)
    data = (root / config['storage']['directory']).resolve()
    profile = read(args.profile)
    profile['display_url'] = f"http://127.0.0.1:{config['display']['port']}/showcase"
    if args.command == 'stop':
        control = data / 'workbuddy/watcher.json'
        if not control.exists():
            print('没有运行中的 WorkBuddy 监听程序。')
            return
        save(data / 'workbuddy/stop.json', {'id': read(control)['id']})
        print('已请求停止 WorkBuddy 监听；当前已提交任务仍由 WorkBuddy 处理。')
        return
    with single_sender(data):
        if args.command == 'watch':
            if config.get('display', {}).get('workbuddy_showcase'):
                try:
                    WorkBuddyDesktop(profile).show_display(profile['display_url'])
                except Exception as error:
                    print('启动展示页失败，可在 WorkBuddy 内打开 ' + profile['display_url'] + '：' + str(error), flush=True)
            watch(root, data, profile)
            return
        if args.command == 'latest':
            records = sorted((data / 'transcripts').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            if not records:
                raise RuntimeError('没有识别记录')
            job = prepare_record(records[0], root, data)
            if job is None:
                raise RuntimeError('最新记录为空、异常挂断或识别失败，未发送')
        else:
            job = args.job.resolve()
        if args.command == 'result':
            state = monitor(job, args.wait)
        else:
            state = send_job(job, profile, resume_skill=getattr(args, 'resume_skill', False))
            if state['state'] == 'submitted' and args.wait:
                state = monitor(job, args.wait)
        if state['state'] in ('waiting_desktop', 'submission_unknown', 'timed_out', 'failed'):
            raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('WorkBuddy 脚本已停止。已提交的任务不会被取消。')
    except Exception as error:
        print('WorkBuddy 脚本暂停：', error, file=sys.stderr)
        raise SystemExit(2)
