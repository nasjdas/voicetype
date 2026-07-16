#!/usr/bin/env python3
"""
Apple Silicon engine — Parakeet through Apple MLX, on the Neural Engine.

The fastest path on a modern Mac and the reason dictation feels instant here.
Lifted from the original dictation.py; nothing outside this file imports mlx.
"""

import threading

from .base import Engine

# English picks the English-only model so it can NEVER drift to another language.
# Swedish / auto use the multilingual model.
PARAKEET_EN = "mlx-community/parakeet-tdt-0.6b-v2"
PARAKEET_MULTI = "mlx-community/parakeet-tdt-0.6b-v3"
WHISPER_FALLBACK = "mlx-community/whisper-large-v3-turbo"


class MlxParakeet(Engine):
    name = "parakeet-mlx"

    def __init__(self):
        self._models = {}       # repo -> loaded model
        self._loading = set()
        self._lock = threading.Lock()

    @staticmethod
    def _repo_for_lang(lang):
        return PARAKEET_EN if lang == "en" else PARAKEET_MULTI

    def _get(self, repo, block=True):
        m = self._models.get(repo)
        if m is not None:
            return m
        if not block:
            with self._lock:
                if repo in self._loading:
                    return None
                self._loading.add(repo)
            threading.Thread(target=self._load, args=(repo,), daemon=True).start()
            return None
        return self._load(repo)

    def _load(self, repo):
        try:
            from parakeet_mlx import from_pretrained
            m = from_pretrained(repo)
            self._models[repo] = m
            return m
        except Exception:
            return None
        finally:
            self._loading.discard(repo)

    def prewarm(self, lang):
        self._get(self._repo_for_lang(lang), block=False)

    def warm_all(self):
        """Preload the multilingual model and trigger the Metal kernel JIT, so the
        first real dictation doesn't pay for it. Metal-specific — stays in this file."""
        try:
            import numpy as np
            import mlx.core as mx
            from parakeet_mlx.audio import get_logmel
            m = self._get(PARAKEET_MULTI, block=True)
            if m is not None:
                mel = get_logmel(mx.array(np.zeros(16000, "float32")), m.preprocessor_config)
                m.generate(mel)
        except Exception:
            pass

    @property
    def ready(self):
        return bool(self._models)

    def transcribe(self, audio, lang):
        repo = self._repo_for_lang(lang)
        m = self._get(repo, block=False)
        if m is None:
            # The chosen model is still downloading. Rather than block, fall back —
            # but never hand non-English audio to the English-ONLY model, which by
            # design cannot drift to another language and would return nonsense.
            for r, loaded in list(self._models.items()):
                if lang == "en" or r != PARAKEET_EN:
                    m = loaded
                    break
        if m is not None:
            try:
                import mlx.core as mx
                from parakeet_mlx.audio import get_logmel
                mel = get_logmel(mx.array(audio), m.preprocessor_config)
                return m.generate(mel)[0].text
            except Exception:
                pass
        import mlx_whisper
        wl = lang if lang in ("en", "sv") else None
        res = mlx_whisper.transcribe(audio, path_or_hf_repo=WHISPER_FALLBACK,
                                     language=wl, condition_on_previous_text=False)
        return res.get("text", "")
