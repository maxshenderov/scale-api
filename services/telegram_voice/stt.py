import os
import tempfile
import subprocess
import logging

import httpx

# Корпоративная сеть MITM — отключаем SSL для httpx до загрузки faster_whisper
_original_client_init = httpx.Client.__init__
def _patched_init(self, *args, **kwargs):
    kwargs['verify'] = False
    _original_client_init(self, *args, **kwargs)
httpx.Client.__init__ = _patched_init

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
MODEL_DIR = os.getenv("WHISPER_MODEL_DIR", "/app/whisper_models")
DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8 for CPU, float16 for GPU


class STT:
    def __init__(self) -> None:
        logger.info(f"Loading Whisper model '{MODEL_SIZE}' on {DEVICE}:{COMPUTE_TYPE}...")
        self.model = WhisperModel(
            MODEL_SIZE,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=MODEL_DIR,
        )
        logger.info("Whisper model loaded")

    def transcribe(self, ogg_path: str) -> str:
        # faster-whisper не ест ogg — конвертируем в wav через ffmpeg
        wav_path = ogg_path + ".wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
                capture_output=True,
                check=True,
                timeout=30,
            )
            segments, _ = self.model.transcribe(wav_path, language="ru")
            return " ".join(seg.text.strip() for seg in segments)
        finally:
            for tmp in (wav_path,):
                if os.path.exists(tmp):
                    os.remove(tmp)
