"""Loopback-only SIP/RTP test using a WAV sample. Does not contact the HT802."""
import argparse
import json
import math
from pathlib import Path
import socket
import struct
import time
import uuid
import wave


def mulaw(sample):
    sign = 128 if sample < 0 else 0
    value = min(32635, abs(int(sample))) + 132
    exponent, mask = 7, 0x4000
    while exponent > 0 and not value & mask:
        exponent -= 1
        mask >>= 1
    return (~(sign | exponent << 4 | ((value >> (exponent + 3)) & 15))) & 255


def run(config_path, wav):
    import numpy as np
    from scipy.signal import resample_poly
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))['phone']
    if config['local_address'] != '127.0.0.1' or config['device_address'] != '127.0.0.1':
        raise ValueError('This simulator is restricted to a separate loopback test configuration')
    with wave.open(str(wav), 'rb') as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError('Mono PCM16 WAV required')
        rate = source.getframerate()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(np.float32)
    common = math.gcd(rate, 8000)
    samples = resample_poly(samples, 8000 // common, rate // common)
    audio = bytes(mulaw(x) for x in samples)
    address = ('127.0.0.1', config['sip_port'])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sip, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rtp:
        sip.bind(('127.0.0.1', 0))
        rtp.bind(('127.0.0.1', 0))
        sip.settimeout(5)
        local_port = sip.getsockname()[1]
        call_id = str(uuid.uuid4())
        remote = f'<sip:263@127.0.0.1:{config["sip_port"]}>'
        def packet(method, seq, body=''):
            return (f'{method} sip:263@127.0.0.1:{config["sip_port"]} SIP/2.0\r\n'
                    f'Via: SIP/2.0/UDP 127.0.0.1:{local_port};branch=z9hG4bK{seq}\r\n'
                    f'From: <sip:test@127.0.0.1:{local_port}>;tag=test\r\nTo: {remote}\r\n'
                    f'Call-ID: {call_id}\r\nCSeq: {seq} {method}\r\n'
                    f'Contact: <sip:test@127.0.0.1:{local_port}>\r\nContent-Type: application/sdp\r\n'
                    f'Content-Length: {len(body.encode())}\r\n\r\n{body}').encode()
        sdp = f'v=0\r\nc=IN IP4 127.0.0.1\r\nm=audio {rtp.getsockname()[1]} RTP/AVP 0 101\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:101 telephone-event/8000\r\n'
        sip.sendto(packet('INVITE', 1, sdp), address)
        while True:
            response = sip.recv(65535).decode()
            if response.startswith('SIP/2.0 200'):
                remote = next(line.split(':', 1)[1].strip() for line in response.split('\r\n') if line.lower().startswith('to:'))
                break
            if not response.startswith('SIP/2.0 100'):
                raise RuntimeError(response.split('\r\n')[0])
        sip.sendto(packet('ACK', 1), address)
        # Let the ready tone complete, then stream in real time.
        audio = bytes([255]) * 4000 + audio + bytes([255]) * 2400
        started = time.monotonic()
        for sequence, offset in enumerate(range(0, len(audio), 160)):
            payload = audio[offset:offset + 160].ljust(160, b'\xff')
            rtp.sendto(struct.pack('!BBHII', 128, 0, sequence % 65536, offset, 12345) + payload, ('127.0.0.1', config['rtp_port']))
            time.sleep(max(0, started + (sequence + 1) * .02 - time.monotonic()))
        bye = packet('BYE', 2)
        sip.sendto(bye, address)
        assert sip.recv(65535).startswith(b'SIP/2.0 200')
        sip.sendto(bye, address)  # Retransmission must not create a second transcript.
        assert sip.recv(65535).startswith(b'SIP/2.0 200')
        print('Simulated pickup, PCMU RTP and duplicate hangup completed:', call_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--wav', type=Path, required=True)
    args = parser.parse_args()
    run(args.config, args.wav)
