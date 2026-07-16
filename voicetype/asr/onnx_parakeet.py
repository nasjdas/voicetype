#!/usr/bin/env python3
"""
Portable engine — the same Parakeet model, run through ONNX Runtime.

Used on Windows and on Intel Macs. Deliberately the SAME weights Apple Silicon
runs via MLX (NVIDIA's parakeet-tdt-0.6b-v3), so this is a different runtime under
a model that's already proven, not a different quality bar.

Why not Whisper: Whisper commits to one language per 30-second window, and a
push-to-talk utterance IS one window — so a Swedish sentence with English words in
it gets forced entirely into one language. Parakeet's unified tokenizer has no such
gate. Whisper on CPU is also far too slow at the accuracy we need (large-v3 runs
slower than realtime), while this is ~24x realtime on plain CPU, no GPU required.

Measured on CPU only (no CoreML, no CUDA), which is what a Windows laptop gets:
    2.7s of speech  → 0.11s
    180s of speech  → 8.2s, complete, nothing truncated
"""

import threading

from .base import Engine

MODEL = "nemo-parakeet-tdt-0.6b-v3"     # ~671 MB at int8, auto-downloads once


class OnnxParakeet(Engine):
    name = "parakeet-onnx"

    def __init__(self):
        self._model = None
        self._loading = False
        self._lock = threading.Lock()

    def _load(self):
        try:
            import onnx_asr
            m = onnx_asr.load_model(MODEL, quantization="int8")
            self._model = m
        except Exception:
            pass
        finally:
            self._loading = False

    def prewarm(self, lang=None):
        # v3 auto-detects the language, so there is nothing per-language to load.
        if self._model is not None:
            return
        with self._lock:
            if self._loading:
                return
            self._loading = True
        threading.Thread(target=self._load, daemon=True).start()

    def warm_all(self):
        self.prewarm()

    @property
    def ready(self):
        return self._model is not None

    def transcribe(self, audio, lang="auto"):
        m = self._model
        if m is None:
            self.prewarm()
            raise RuntimeError("speech model is still loading")
        out = m.recognize(audio)
        # A VAD-wrapped model yields segments instead of a string.
        if not isinstance(out, str):
            out = " ".join(getattr(s, "text", str(s)) for s in out)
        return out
