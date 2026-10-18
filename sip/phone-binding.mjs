export class DtmfDecoder {
  constructor() { this.seen = new Set(); }
  read({ payload, timestamp }) {
    if (payload.length < 4 || payload[0] > 15 || payload[1] & 64) return null;
    // RFC 4733 repeats ongoing/end packets; timestamp + event identifies a key.
    const key = `${timestamp}:${payload[0]}`;
    if (this.seen.has(key)) return null;
    this.seen.add(key);
    if (this.seen.size > 256) this.seen.delete(this.seen.values().next().value);
    return '0123456789*#ABCD'[payload[0]];
  }
}

