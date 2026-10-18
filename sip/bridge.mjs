// SIP/RTP transport adapted from prikevs/codex-redline (MIT; see LICENSE).
import { PhoneSip } from './phone-sip.mjs';
import { tone, decodeMuLaw } from './phone-audio.mjs';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import readline from 'node:readline';
import { CompletionNotifier } from './completion-notifier.mjs';

const cfgPath = resolve(process.argv[2]);
const cfg = JSON.parse(readFileSync(cfgPath, 'utf8').replace(/^\uFEFF/, ''));
const cfgPhone = cfg.phone;
const recordings = resolve(dirname(cfgPath), cfg.storage.directory, 'recordings');
mkdirSync(recordings, { recursive: true });
const emit = (event, extra = {}) => console.log(JSON.stringify({ event, time: new Date().toISOString(), ...extra }));
const phone = new PhoneSip({ host: cfgPhone.local_address, peer: cfgPhone.device_address,
  port: cfgPhone.sip_port, peerPort: cfgPhone.device_sip_port, rtpPort: cfgPhone.rtp_port });
let ready = false, active = null, stopping = false;
const notifier = new CompletionNotifier(phone, emit,
  Buffer.concat([tone(660, .3), Buffer.alloc(1600, 255), tone(880, .3)]),
  Math.max(2, Math.min(20, cfg.notifications?.ring_seconds ?? 8)) * 1000);
phone.allowIncoming = () => ready && !active;
phone.on('failure', error => emit('error', { error: error.message }));
phone.on('incoming', () => { ready = false; emit('pickup'); });
phone.on('connected', call => {
  if (notifier.connected(call)) return;
  clearTimeout(call.confirmTimer);
  ready = false;
  active = { id: randomUUID(), callId: call.id, chunks: [], bytes: 0, started: Date.now(), lastAudio: Date.now() };
  emit('recording', { id: active.id });
  phone.play(tone(660, .3)).catch(() => {});
});
phone.on('audio', (data, call) => {
  if (!active || active.callId !== call.id) return;
  active.lastAudio = Date.now();
  if (active.bytes + data.length > cfgPhone.max_seconds * 8000) { phone.hangup('duration_limit'); return; }
  active.chunks.push(Buffer.from(data)); active.bytes += data.length;
});
function toWav(bytes) {
  const wav = Buffer.alloc(44 + bytes.length * 2);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(bytes.length * 2, 40);
  for (let i = 0; i < bytes.length; i++) wav.writeInt16LE(decodeMuLaw(bytes[i]), 44 + i * 2);
  return wav;
}
phone.on('ended', ({ call, reason }) => {
  if (notifier.ended(call, reason)) return;
  const rec = active; active = null;
  if (!rec || rec.callId !== call.id) { emit('call_ended', { reason }); return; }
  try {
    const file = join(recordings, rec.id + '.wav');
    writeFileSync(file, toWav(Buffer.concat(rec.chunks)));
    emit('hangup', { id: rec.id, reason, file, seconds: rec.bytes / 8000, packets: call.received });
  } catch (error) { emit('error', { error: '录音保存失败：' + error.message }); }
});
const heartbeat = setInterval(() => {
  if (active && Date.now() - active.lastAudio > cfgPhone.media_timeout_seconds * 1000) phone.hangup('media_timeout');
  if (active && Date.now() - active.started > cfgPhone.max_seconds * 1000) phone.hangup('duration_limit');
  emit('heartbeat', { connected: Boolean(phone.call?.connected), seconds: active ? (Date.now() - active.started) / 1000 : 0,
    packets: phone.call?.received ?? 0 });
}, 1000);
function stop() {
  if (stopping) return; stopping = true; clearInterval(heartbeat);
  try { phone.close(); } catch {}
  process.exit(0);
}
const commands = readline.createInterface({ input: process.stdin });
commands.on('line', line => {
  try {
    const msg = JSON.parse(line);
    if (msg.command === 'ready') ready = Boolean(msg.value);
    if (msg.command === 'notify' && /^[a-f0-9-]{36}$/.test(msg.task_id)) notifier.request(msg.task_id, ready);
    if (msg.command === 'stop') stop();
  } catch (error) { emit('error', { error: error.message }); }
});
commands.on('close', stop);
process.on('SIGINT', stop); process.on('SIGTERM', stop);
try { await phone.start(); emit('listening', { address: phone.host, port: phone.port }); }
catch (error) { emit('fatal', { error: '电话服务启动失败，请检查有线网卡 IP 和端口占用：' + error.message }); stop(); }
