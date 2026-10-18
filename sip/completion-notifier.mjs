// Outgoing completion calls never enter the recording/ASR path.
export class CompletionNotifier {
  constructor(phone, emit, sound, durationMs = 8000) {
    Object.assign(this, { phone, emit, sound, durationMs });
    this.seen = new Set();
    this.timer = null;
    phone.on('signal', ({code}) => {
      if (phone.call?.purpose === 'completion' && code === 180)
        this.report(phone.call, 'ringing', 'HT802已返回响铃信号，图片已生成');
    });
  }
  report(call, state, message) {
    this.emit('notification', {task_id: call.taskId, state, message});
  }
  request(taskId, ready) {
    if (this.seen.has(taskId)) return;
    if (!ready || this.phone.call) {
      this.emit('notification', {task_id: taskId, state: 'pending', message: '电话使用中，稍后提醒'});
      return;
    }
    const call = this.phone.dial({ringDurationMs: this.durationMs});
    call.purpose = 'completion'; call.taskId = taskId;
    this.seen.add(taskId);
    this.report(call, 'dialing', '已呼叫座机，等待响铃信号');
    this.timer = setTimeout(() => {
      if (this.phone.call === call) this.phone.hangup('notification_timeout');
    }, this.durationMs + 5000);
  }
  connected(call) {
    if (call.purpose !== 'completion') return false;
    clearTimeout(call.confirmTimer);
    this.report(call, 'answered', '已接听生成完成提醒');
    this.phone.play(this.sound).catch(() => {}).finally(() => {
      if (this.phone.call === call) this.phone.hangup('notification_complete');
    });
    return true;
  }
  ended(call, reason) {
    if (call.purpose !== 'completion') return false;
    clearTimeout(this.timer);
    const ok = call.connected || call.ringStarted;
    this.report(call, ok ? 'done' : 'failed', ok ? '提醒呼叫已结束，实际铃声请核对座机' : '座机提醒未接通：' + reason);
    return true;
  }
}
