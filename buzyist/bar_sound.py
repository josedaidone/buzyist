"""
Sounds for the BUSY Bar's speaker, made here rather than shipped as files.

The bar plays `.snd` files: mono 16-bit little-endian PCM with no header
(its own sounds under /ext/apps_assets read that way), at 44.1 kHz as far
as the ear can tell. Only its `shared/` stock sounds can be played by
another app, and they are quiet, so the app uploads its own
(Bar.play_sound). `tools/bar_sound_lab.py` makes and tries more of them.
"""

from __future__ import annotations

import functools
import hashlib
import math
import struct

RATE = 44100
MAX_SECONDS = 10

# The reminder chime (picked in the sound lab): three short square beeps on a
# G6, quiet enough not to clip on the bar's small speaker.
CHIME = {
    "pattern": "beeps",
    "shape": "square",
    "freq": 1568,
    "beep_ms": 260,
    "gap_ms": 60,
    "beeps": 3,
    "gain": 0.3,
    "rate": RATE,
}


def tone(spec: dict) -> bytes:
    """
    A sound as the bar's .snd files hold it, from a spec: `pattern` "beeps"
    (each at `freq`), "two-tone" (alternating `freq` and `freq2`) or "sweep"
    (`freq` to `freq2` within each beep); `shape` "square", "sine" or
    "triangle"; `beeps` of `beep_ms` each followed by `gap_ms` of silence;
    `gain` 0..1; `rate` in Hz. ValueError past MAX_SECONDS.
    """
    rate = int(spec.get("rate", RATE))
    freq = float(spec.get("freq", 2000))
    freq2 = float(spec.get("freq2", 0)) or freq
    beep_s = max(0.01, float(spec.get("beep_ms", 150)) / 1000)
    gap_s = max(0.0, float(spec.get("gap_ms", 80)) / 1000)
    beeps = max(1, int(spec.get("beeps", 4)))
    gain = min(1.0, max(0.01, float(spec.get("gain", 1.0))))
    shape = spec.get("shape", "square")
    pattern = spec.get("pattern", "beeps")
    if beeps * (beep_s + gap_s) > MAX_SECONDS:
        raise ValueError(f"longer than {MAX_SECONDS} s")

    fade = min(int(rate * 0.004), int(rate * beep_s / 4))  # 4 ms in and out: no clicks
    out = bytearray()
    phase = 0.0
    for n in range(beeps):
        count = int(rate * beep_s)
        for i in range(count):
            if pattern == "sweep":
                f = freq + (freq2 - freq) * i / count
            elif pattern == "two-tone":
                f = freq if n % 2 == 0 else freq2
            else:
                f = freq
            phase += 2 * math.pi * f / rate
            value = math.sin(phase)
            if shape == "square":
                value = 0.8 if value >= 0 else -0.8  # 0.8: a square at full scale clips hard
            elif shape == "triangle":
                value = 2 / math.pi * math.asin(value)
            envelope = min(1.0, i / fade if fade else 1.0, (count - i) / fade if fade else 1.0)
            out += struct.pack("<h", int(max(-1.0, min(1.0, value * gain * envelope)) * 32767))
        out += b"\x00\x00" * int(rate * gap_s)
    return bytes(out)


@functools.cache
def chime() -> bytes:
    """The reminder chime (CHIME), made once."""
    return tone(CHIME)


def sound_name(sound: bytes) -> str:
    """The file name a sound goes under on the bar: from its content, like the pictures (bar.picture_name)."""
    return "sound-" + hashlib.sha256(sound).hexdigest()[:16] + ".snd"
