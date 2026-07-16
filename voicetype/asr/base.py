#!/usr/bin/env python3
"""
The speech-engine contract.

One interface, so the rest of the app never learns which engine it's talking to.

The contract was free: TARGET_RATE has always been 16000 and the recorder already
resamples to float32 mono at that rate before transcribing. Every engine here
wants exactly that, so the seam costs nothing.
"""

from abc import ABC, abstractmethod

TARGET_RATE = 16000


class Engine(ABC):
    """A local speech-to-text engine."""

    name = "engine"

    @abstractmethod
    def prewarm(self, lang):
        """Start loading/JITing the model for `lang`. Non-blocking, idempotent."""

    @abstractmethod
    def transcribe(self, audio, lang):
        """audio: float32 mono @ 16 kHz. lang: 'en' | 'sv' | 'auto'.

        Returns RAW text — text.clean_text() owns all cleanup.
        Must not block on a model download; degrade to a ready model instead.
        """

    @property
    @abstractmethod
    def ready(self):
        """True once at least one model is loaded and can transcribe now."""
