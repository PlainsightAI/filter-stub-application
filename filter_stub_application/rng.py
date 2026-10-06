"""Deterministic RNG substreams.

Kept in its own module so process and generator can share seeds without a
circular import.
"""

from __future__ import annotations

import hashlib
import random


def stream(master_seed: int, instance_id: int, purpose: str) -> random.Random:
    """Stable substream. Adding a payload field does not move the arrival stream."""
    digest = hashlib.sha256(f"{master_seed}:{instance_id}:{purpose}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
