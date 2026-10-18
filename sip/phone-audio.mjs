export function decodeMuLaw(byte) {
  const value = (~byte) & 255;
  const sample = (((value & 15) << 3) + 132) << ((value >> 4) & 7);
  return value & 128 ? 132 - sample : sample - 132;
}

export function encodeMuLaw(sample) {
  const sign = sample < 0 ? 128 : 0;
  let value = Math.min(32635, Math.abs(Math.round(sample))) + 132;
  let exponent = 7;
  for (let mask = 0x4000; exponent > 0 && !(value & mask); mask >>= 1) exponent--;
  return (~(sign | (exponent << 4) | ((value >> (exponent + 3)) & 15))) & 255;
}

export function muLawToPcm24k(payload) {
  // Integer-rate interpolation of the 8 kHz telephone stream for the STT API.
  const out = Buffer.alloc(payload.length * 6);
  for (let i = 0; i < payload.length; i++) {
    const a = decodeMuLaw(payload[i]);
    const b = decodeMuLaw(payload[Math.min(i + 1, payload.length - 1)]);
    for (let j = 0; j < 3; j++) out.writeInt16LE(Math.round(a + (b - a) * j / 3), i * 6 + j * 2);
  }
  return out;
}

export function parseRtp(packet) {
  if (packet.length < 12 || packet[0] >> 6 !== 2) return null;
  let offset = 12 + 4 * (packet[0] & 15);
  if (offset > packet.length) return null;
  if (packet[0] & 16) {
    if (offset + 4 > packet.length) return null;
    offset += 4 + 4 * packet.readUInt16BE(offset + 2);
  }
  const padding = packet[0] & 32 ? packet.at(-1) : 0;
  if (offset > packet.length - padding || ((packet[0] & 32) && !padding)) return null;
  return { payloadType: packet[1] & 127, sequence: packet.readUInt16BE(2), timestamp: packet.readUInt32BE(4), payload: packet.subarray(offset, packet.length - padding) };
}

export function makeRtp(payload, sequence, timestamp, ssrc) {
  const header = Buffer.alloc(12);
  header[0] = 128;
  header.writeUInt16BE(sequence & 65535, 2);
  header.writeUInt32BE(timestamp >>> 0, 4);
  header.writeUInt32BE(ssrc >>> 0, 8);
  return Buffer.concat([header, payload]);
}

export function tone(frequency = 660, seconds = 0.15) {
  return Buffer.from(Array.from({ length: Math.round(8000 * seconds) }, (_, i) => encodeMuLaw(2500 * Math.sin(2 * Math.PI * frequency * i / 8000))));
}

