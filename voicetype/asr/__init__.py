#!/usr/bin/env python3
"""Pick the speech engine that fits this machine."""

import os
import platform
import sys

from .base import Engine, TARGET_RATE

__all__ = ["Engine", "TARGET_RATE", "get_engine"]


def get_engine():
    """Apple Silicon → MLX (Neural Engine, fastest).
    Everything else (Windows, Intel Mac) → the same model via ONNX Runtime.

    VOICETYPE_ENGINE=onnx|mlx overrides, which is how the ONNX path gets tested
    on a Mac without owning a Windows machine.
    """
    want = (os.environ.get("VOICETYPE_ENGINE") or "").strip().lower()
    if want == "onnx":
        from .onnx_parakeet import OnnxParakeet
        return OnnxParakeet()
    if want == "mlx":
        from .mlx_parakeet import MlxParakeet
        return MlxParakeet()

    if sys.platform == "darwin" and platform.machine() == "arm64":
        from .mlx_parakeet import MlxParakeet
        return MlxParakeet()
    from .onnx_parakeet import OnnxParakeet
    return OnnxParakeet()
