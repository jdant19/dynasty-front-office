"""Strict JSON for the front door page. Browsers reject NaN and Infinity, and one NaN anywhere makes the
whole brief unreadable (2026-11-24: a bench player's empty slot written as NaN blanked the page)."""
from __future__ import annotations

import json
import math

import numpy as np


def clean(x):
    """NaN and infinities become null; numpy numbers become plain numbers."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    if x is getattr(np, "nan", object()):
        return None
    return x


def dumps(x, **kw) -> str:
    return json.dumps(clean(x), default=str, allow_nan=False, **kw)
