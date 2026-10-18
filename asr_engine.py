"""Local Qwen3-ASR adapter. Device, precision and model are configuration values."""
import importlib.metadata
import time
import wave
from pathlib import Path


def choose_device(torch, requested='auto', precision='auto'):
    device = ('cuda:0' if torch.cuda.is_available() else 'cpu') if requested == 'auto' else requested
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('CUDA 不可用。检查显卡驱动及 PyTorch 安装，或将 device 改为 cpu。')
    if precision == 'auto':
        precision = 'float16' if device.startswith('cuda') else 'float32'
    if precision not in ('float16', 'float32', 'bfloat16'):
        raise ValueError('dtype 必须为 auto / float16 / float32 / bfloat16')
    if device == 'cpu' and precision == 'float16':
        raise ValueError('CPU 模式请使用 float32 或 auto')
    return device, getattr(torch, precision), precision


class AsrEngine:
    def __init__(self, config, root):
        import numpy as np
        import torch
        from qwen_asr import Qwen3ASRModel
        from opencc import OpenCC
        self.np, self.torch, self.config = np, torch, config
        self.device, dtype, precision = choose_device(torch, config['device'], config['dtype'])
        self.converter = OpenCC('t2s') if config['simplified'] else None
        model_path = (Path(root) / config['model_path']).resolve()
        if not (model_path / 'config.json').is_file():
            raise FileNotFoundError(f'未找到模型：{model_path}。先运行 scripts/download_model.py。')
        self.model = Qwen3ASRModel.from_pretrained(str(model_path), dtype=dtype,
            device_map=self.device, local_files_only=True, max_inference_batch_size=1,
            max_new_tokens=config['max_new_tokens'])
        self.info = {'model': model_path.name, 'device': self.device, 'dtype': precision,
                     'torch': torch.__version__, 'cuda_runtime': torch.version.cuda,
                     'qwen_asr': importlib.metadata.version('qwen-asr')}
        if self.device.startswith('cuda'):
            props = torch.cuda.get_device_properties(self.device)
            self.info.update(gpu=props.name, vram_gb=round(props.total_memory / 2**30, 2))

    def transcribe(self, path):
        np, torch = self.np, self.torch
        with wave.open(str(path), 'rb') as audio_file:
            if audio_file.getnchannels() != 1 or audio_file.getsampwidth() != 2:
                raise ValueError('录音必须为单声道 16 位 PCM WAV')
            rate = audio_file.getframerate()
            if rate not in (8000, 16000, 24000, 44100, 48000):
                raise ValueError('不支持的录音采样率')
            data = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype='<i2').astype(np.float32) / 32768
        if len(data) < rate / 2 or float(np.sqrt(np.mean(data * data))) < self.config['silence_rms']:
            return {'text': '', 'raw_text': '', 'language': '', 'asr_seconds': 0, 'empty': True}
        if self.device.startswith('cuda'):
            torch.cuda.reset_peak_memory_stats(self.device)
        started = time.monotonic()
        with torch.inference_mode():
            result = self.model.transcribe(audio=(data, rate), language=self.config['language'], context=self.config['context'])[0]
        raw = result.text.strip()
        text = self.converter.convert(raw) if self.converter else raw
        return {'text': text, 'raw_text': raw, 'language': result.language,
                'asr_seconds': round(time.monotonic() - started, 2),
                'peak_vram_gb': round(torch.cuda.max_memory_reserved(self.device) / 2**30, 2) if self.device.startswith('cuda') else None,
                'empty': not text}
