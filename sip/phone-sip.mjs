import dgram from 'node:dgram';
import { EventEmitter } from 'node:events';
import { randomBytes, randomUUID } from 'node:crypto';
import { makeRtp, parseRtp } from './phone-audio.mjs';
import { DtmfDecoder } from './phone-binding.mjs';

function port(name, fallback) {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1024 || value > 65535) throw new Error(`${name} 必须是 1024 到 65535 之间的端口`);
  return value;
}

export function parseSip(buffer) {
  if (buffer.length > 65507) return null;
  const text = buffer.toString('utf8');
  const end = text.indexOf('\r\n\r\n');
  if (end < 0) return null;
  const [first, ...lines] = text.slice(0, end).split('\r\n');
  const headers = {};
  for (const line of lines) {
    const colon = line.indexOf(':');
    if (colon < 1) continue;
    const key = line.slice(0, colon).toLowerCase();
    (headers[key] ??= []).push(line.slice(colon + 1).trim());
  }
  if (!headers['call-id'] || !headers.cseq) return null;
  return { first, headers, body: text.slice(end + 4), id: headers['call-id'][0] };
}

export function mediaAddress(body, peer) {
  const match = body.match(/^m=audio (\d+) RTP\/AVP ([\d ]+)/m);
  const address = body.match(/^c=IN IP4 ([\d.]+)/m)?.[1];
  const port = Number(match?.[1]);
  if (!match || !match[2].trim().split(/ +/).includes('0') || address !== peer || port < 1024 || port > 65535) return null;
  return { address, port };
}

const header = (message, name) => message.headers[name]?.[0] ?? '';
const uriFrom = text => text.match(/<([^>]+)>/)?.[1] ?? text.split(';')[0];

export class PhoneSip extends EventEmitter {
  constructor({ host = process.env.HT802_LOCAL_ADDRESS ?? '192.168.82.1', peer = process.env.HT802_ADDRESS ?? '192.168.82.100', port: sipPort = port('HT802_SIP_PORT', 5090), peerPort = port('HT802_DEVICE_SIP_PORT', 5060), rtpPort = port('HT802_RTP_PORT', 15004) } = {}) {
    super(); Object.assign(this, { host, peer, port: sipPort, peerPort, rtpPort });
    this.call = null; this.allowIncoming = () => true; this.replies = new Map(); this.closed = false;
  }
  async start() {
    this.sip = dgram.createSocket('udp4'); this.rtp = dgram.createSocket('udp4');
    this.sip.on('message', (data, addr) => {
      if (addr.address !== this.peer) return;
      try { this.receiveSip(data, addr); } catch (error) { this.emit('failure', error); }
    });
    this.rtp.on('message', (data, addr) => {
      const call = this.call;
      if (!call?.connected || addr.address !== this.peer || addr.port !== call.media?.port) return;
      const packet = parseRtp(data);
      if (!packet || !packet.payload.length) return;
      if (packet.payloadType === call.dtmfPayloadType) {
        const digit = call.dtmf.read(packet);
        if (digit !== null) this.emit('digit', digit, call);
        return;
      }
      if (packet.payloadType !== 0) return;
      if (call.lastSequence !== undefined) {
        const step = (packet.sequence - call.lastSequence + 65536) % 65536;
        if (!step || step > 32768) return;
        const missing = Math.min(step - 1, 10);
        if (missing) this.emit('audio', Buffer.alloc(missing * 160, 255), call);
      }
      call.lastSequence = packet.sequence; call.received++;
      this.emit('audio', packet.payload, call);
    });
    await Promise.all([this.bind(this.sip, this.port), this.bind(this.rtp, this.rtpPort)]);
    this.sip.on('error', e => this.emit('failure', e)); this.rtp.on('error', e => this.emit('failure', e));
  }
  bind(socket, port) {
    return new Promise((resolve, reject) => { socket.once('error', reject); socket.bind(port, this.host, () => { socket.removeListener('error', reject); resolve(); }); });
  }
  sdp(dtmfPayloadType = 101) { return `v=0\r\no=redline 1 1 IN IP4 ${this.host}\r\ns=REDLINE\r\nc=IN IP4 ${this.host}\r\nt=0 0\r\nm=audio ${this.rtpPort} RTP/AVP 0 ${dtmfPayloadType}\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:${dtmfPayloadType} telephone-event/8000\r\na=fmtp:${dtmfPayloadType} 0-15\r\na=ptime:20\r\na=sendrecv\r\n`; }
  send(packet, addr = { address: this.peer, port: this.peerPort }) { if (!this.closed) this.sip.send(Buffer.from(packet), addr.port, addr.address); }
  response(request, status, reason, body = '', tag = '', addr) {
    const lines = [`SIP/2.0 ${status} ${reason}`];
    for (const name of ['via', 'from', 'to', 'call-id', 'cseq']) {
      for (let value of request.headers[name] ?? []) {
        if (name === 'to' && tag && !/;tag=/.test(value)) value += `;tag=${tag}`;
        lines.push(`${name}: ${value}`);
      }
    }
    lines.push(`Contact: <sip:redline@${this.host}:${this.port}>`, 'Allow: INVITE, ACK, CANCEL, BYE, OPTIONS', 'Server: REDLINE/0.2');
    if (body) lines.push('Content-Type: application/sdp');
    const packet = `${lines.join('\r\n')}\r\nContent-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`;
    this.send(packet, addr); return packet;
  }
  request(call, method, { sameTransaction = false } = {}) {
    const branch = sameTransaction ? call.branch : `z9hG4bK${randomUUID()}`;
    const seq = ['ACK', 'CANCEL'].includes(method) ? 1 : ++call.localSeq;
    const body = method === 'INVITE' ? this.sdp() : '';
    const lines = [`${method} ${call.target} SIP/2.0`, `Via: SIP/2.0/UDP ${this.host}:${this.port};branch=${branch};rport`, 'Max-Forwards: 70', `From: ${call.local}`, `To: ${call.remote}`, `Call-ID: ${call.id}`, `CSeq: ${seq} ${method}`, `Contact: <sip:redline@${this.host}:${this.port}>`, 'User-Agent: REDLINE/0.2'];
    if (body) lines.push('Content-Type: application/sdp');
    const packet = `${lines.join('\r\n')}\r\nContent-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`;
    this.send(packet, call.signalAddress); return packet;
  }
  newCall(values) {
    return { sequence: 0, timestamp: 0, ssrc: randomBytes(4).readUInt32BE(), received: 0, sent: 0, localSeq: 0, connected: false, playback: null, dtmf: new DtmfDecoder(), dtmfPayloadType: 101, ...values };
  }
  receiveSip(data, addr) {
    const message = parseSip(data); if (!message) return;
    const method = message.first.split(' ')[0];
    const cached = this.replies.get(`${message.id}:${header(message, 'cseq')}`);
    if (cached && method !== 'ACK') return this.send(cached, addr);
    if (method === 'OPTIONS') { this.response(message, 200, 'OK', '', '', addr); return; }
    if (method === 'INVITE') {
      if (this.call?.id === message.id && !this.call.outgoing) {
        this.send(this.call.answerPacket, addr); return;
      }
      if (this.call || !this.allowIncoming()) { this.response(message, 486, 'Busy Here', '', '', addr); return; }
      const media = mediaAddress(message.body, this.peer);
      if (!media) { this.response(message, 488, 'Not Acceptable Here', '', '', addr); return; }
      const tag = randomUUID();
      const offeredDtmf = Number(message.body.match(/^a=rtpmap:(\d+) telephone-event\/8000\s*$/im)?.[1]);
      if (!Number.isInteger(offeredDtmf) || offeredDtmf < 96 || offeredDtmf > 127) { this.response(message, 488, 'Telephone Event Required', '', '', addr); return; }
      const call = this.newCall({ id: message.id, outgoing: false, tag, media, dtmfPayloadType: offeredDtmf, signalAddress: addr, local: `${header(message, 'to')};tag=${tag}`, remote: header(message, 'from'), target: uriFrom(header(message, 'contact')) });
      this.call = call;
      this.response(message, 100, 'Trying', '', '', addr);
      call.answerPacket = this.response(message, 200, 'OK', this.sdp(offeredDtmf), tag, addr);
      call.retryTimer = setInterval(() => this.send(call.answerPacket, addr), 1000);
      call.ackTimeout = setTimeout(() => this.endCall('ack_timeout'), 15000);
      this.emit('incoming', call); return;
    }
    const call = this.call;
    if (!call || call.id !== message.id) {
      if (method !== 'ACK' && !message.first.startsWith('SIP/2.0')) this.response(message, 481, 'Call Does Not Exist', '', '', addr);
      return;
    }
    if (method === 'ACK' && !call.outgoing) {
      if (!call.connected) this.connectCall(call);
      return;
    }
    if (method === 'BYE') {
      const packet = this.response(message, 200, 'OK', '', '', addr);
      const key = `${message.id}:${header(message, 'cseq')}`;
      this.replies.set(key, packet); setTimeout(() => this.replies.delete(key), 32000).unref();
      this.endCall('hangup'); return;
    }
    if (method === 'CANCEL') { this.response(message, 200, 'OK', '', '', addr); this.endCall('cancelled'); return; }
    if (message.first.startsWith('SIP/2.0') && call.outgoing && /INVITE$/.test(header(message, 'cseq'))) {
      const code = Number(message.first.split(' ')[1]);
      this.emit('signal', { code, text: message.first });
      if (code === 180 && call.ringDurationMs && !call.ringStarted) {
        call.ringStarted = true;
        call.confirmTimer = setTimeout(() => this.hangup('confirmation_complete'), call.ringDurationMs);
      }
      if (code >= 100 && code < 200) { clearInterval(call.retryTimer); return; }
      if (code === 200) {
        call.remote = header(message, 'to'); call.target = uriFrom(header(message, 'contact')) || call.target;
        this.request(call, 'ACK');
        if (!call.connected) {
          const media = mediaAddress(message.body, this.peer);
          if (!media) { this.hangup('unsupported_audio'); return; }
          call.media = media; this.connectCall(call);
        }
      } else if (code >= 300) {
        call.remote = header(message, 'to'); this.request(call, 'ACK', { sameTransaction: true });
        this.endCall(code === 486 ? 'busy' : `sip_${code}`);
      }
    }
  }
  connectCall(call) {
    clearInterval(call.retryTimer); clearTimeout(call.ackTimeout); clearTimeout(call.ringTimeout);
    call.connected = true;
    call.mediaTimer = setInterval(() => {
      if (this.call !== call || !call.media) return;
      let payload = Buffer.alloc(160, 255);
      if (call.playback) {
        const chunk = call.playback.data.subarray(call.playback.offset, call.playback.offset + 160);
        chunk.copy(payload); call.playback.offset += chunk.length;
        if (call.playback.offset >= call.playback.data.length) {
          const done = call.playback.resolve; call.playback = null; setTimeout(done, 40);
        }
      }
      this.rtp.send(makeRtp(payload, call.sequence++, call.timestamp, call.ssrc), call.media.port, call.media.address);
      call.timestamp = (call.timestamp + 160) >>> 0; call.sent++;
    }, 20);
    call.maxDuration = setTimeout(() => this.hangup('duration_limit'), 5 * 60 * 1000);
    this.emit('connected', call);
  }
  dial({ ringDurationMs = null } = {}) {
    if (this.call) throw new Error('电话线路正忙');
    const target = `sip:redline@${this.peer}:${this.peerPort}`;
    const call = this.newCall({ outgoing: true, ringDurationMs, id: `${randomUUID()}@${this.host}`, branch: `z9hG4bK${randomUUID()}`, local: `"Codex" <sip:redline@${this.host}:${this.port}>;tag=${randomUUID()}`, remote: `<${target}>`, target, signalAddress: { address: this.peer, port: this.peerPort } });
    this.call = call;
    const packet = this.request(call, 'INVITE', { sameTransaction: true });
    call.retryTimer = setInterval(() => this.send(packet, call.signalAddress), 1000);
    call.ringTimeout = setTimeout(() => { this.request(call, 'CANCEL', { sameTransaction: true }); this.endCall('unanswered'); }, 45000);
    return call;
  }
  play(data) {
    const call = this.call;
    if (!call?.connected) return Promise.reject(new Error('电话未接通'));
    if (call.playback) return Promise.reject(new Error('已有音频正在播放'));
    if (!data.length) return Promise.resolve();
    return new Promise((resolve, reject) => { call.playback = { data, offset: 0, resolve, reject }; });
  }
  hangup(reason = 'local_hangup') {
    if (!this.call) return;
    this.request(this.call, this.call.connected ? 'BYE' : 'CANCEL', { sameTransaction: !this.call.connected });
    this.endCall(reason);
  }
  endCall(reason) {
    const call = this.call; if (!call) return;
    call.endReason = reason;
    clearInterval(call.retryTimer); clearInterval(call.mediaTimer);
    clearTimeout(call.ackTimeout); clearTimeout(call.ringTimeout); clearTimeout(call.maxDuration); clearTimeout(call.confirmTimer);
    call.playback?.reject(new Error('电话已挂断')); this.call = null;
    this.emit('ended', { call, reason });
  }
  close() { this.hangup('shutdown'); this.closed = true; this.sip?.close(); this.rtp?.close(); }
}
