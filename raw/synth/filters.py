"""The filter stack: spectral emphasis, retro filters, and destructive
bit/rate reduction (spec section 100).

Filters run on the rendered buffer, downstream of the phase integral. They
therefore cannot affect the pitch trajectory — pitch identity under any
amount of filtering is guaranteed structurally rather than by policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from .trajectory import Trajectory

try:  # optional accelerator; the pure-Python path below is the fallback
    from scipy.signal import lfilter as _scipy_lfilter
except Exception:  # pragma: no cover - scipy is optional
    _scipy_lfilter = None

FILTER_TYPES = (
    "lowpass",
    "highpass",
    "bandpass",
    "notch",
    "low_shelf",
    "high_shelf",
    "emphasis",
    "bitcrush",
    "sr_reduce",
    "drive",
    "delay",
    "reverb",
    "denoise",
    "sustain",
)

# Effects that make the signal longer than they found it. The stack recomputes
# its normalised time axis after these so later nodes stay in step.
LENGTHENING_TYPES = ("delay", "reverb")

# Which parameters each type exposes, with defaults. Used by the GUI to build
# an editor for any filter type without knowing about it in advance.
FILTER_PARAMS: dict[str, dict[str, float]] = {
    "lowpass": {"cutoff": 4000.0, "resonance": 0.707},
    "highpass": {"cutoff": 200.0, "resonance": 0.707},
    "bandpass": {"center": 1200.0, "width": 1.0},
    "notch": {"center": 1200.0, "width": 1.0},
    "low_shelf": {"corner": 250.0, "gain_db": 0.0},
    "high_shelf": {"corner": 4000.0, "gain_db": 0.0},
    "emphasis": {"center": 1800.0, "width": 1.0, "amount": 6.0},
    "bitcrush": {"bits": 8.0},
    "sr_reduce": {"target_rate": 11025.0},
    "drive": {"amount": 2.0},
    "delay": {"time": 0.25, "feedback": 0.35, "mix": 0.35},
    "reverb": {"size": 1.2, "damping": 0.4, "mix": 0.3, "predelay": 0.02},
    "denoise": {"amount": 0.6, "floor": 0.08},
    "sustain": {"amount": 0.5, "attack": 0.01, "release": 0.25},
}

# Sensible editor ranges. The dialog guessed from parameter names, which broke
# down once "amount" meant dB for emphasis, a gain for drive and 0..1 here.
FILTER_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "lowpass": {"cutoff": (10.0, 20000.0), "resonance": (0.05, 20.0)},
    "highpass": {"cutoff": (10.0, 20000.0), "resonance": (0.05, 20.0)},
    "bandpass": {"center": (10.0, 20000.0), "width": (0.05, 6.0)},
    "notch": {"center": (10.0, 20000.0), "width": (0.05, 6.0)},
    "low_shelf": {"corner": (10.0, 20000.0), "gain_db": (-24.0, 24.0)},
    "high_shelf": {"corner": (10.0, 20000.0), "gain_db": (-24.0, 24.0)},
    "emphasis": {"center": (10.0, 20000.0), "width": (0.05, 6.0), "amount": (-24.0, 24.0)},
    "bitcrush": {"bits": (1.0, 24.0)},
    "sr_reduce": {"target_rate": (100.0, 48000.0)},
    "drive": {"amount": (0.1, 40.0)},
    "delay": {"time": (0.001, 5.0), "feedback": (0.0, 0.95), "mix": (0.0, 1.0)},
    "reverb": {"size": (0.05, 8.0), "damping": (0.0, 1.0), "mix": (0.0, 1.0),
               "predelay": (0.0, 0.5)},
    "denoise": {"amount": (0.0, 1.0), "floor": (0.0, 1.0)},
    "sustain": {"amount": (0.0, 1.0), "attack": (0.001, 0.5), "release": (0.01, 3.0)},
}


def param_range(kind: str, name: str) -> tuple[float, float]:
    return FILTER_RANGES.get(kind, {}).get(name, (-48.0, 48.0))

MAX_Q = 20.0
_BLOCK = 128  # samples between coefficient updates for trajectory-valued params


@dataclass
class FilterNode:
    type: str = "lowpass"
    params: dict = field(default_factory=dict)
    enabled: bool = True
    id: str = "f"

    def param(self, name: str):
        if name in self.params:
            return self.params[name]
        return FILTER_PARAMS.get(self.type, {}).get(name, 0.0)

    def to_dict(self) -> dict:
        out = {"id": self.id, "type": self.type, "enabled": self.enabled}
        for k, v in self.params.items():
            out[k] = v.to_dict() if isinstance(v, Trajectory) else v
        return out

    @staticmethod
    def from_dict(d: dict) -> "FilterNode":
        reserved = {"id", "type", "enabled"}
        params = {}
        for k, v in d.items():
            if k in reserved:
                continue
            params[k] = Trajectory.from_dict(v) if isinstance(v, dict) else v
        return FilterNode(
            type=d.get("type", "lowpass"),
            params=params,
            enabled=bool(d.get("enabled", True)),
            id=d.get("id", "f"),
        )


# --------------------------------------------------------------------- maths


def q_from_octaves(width: float) -> float:
    """Convert a bandwidth in octaves to a biquad Q (spec section 100.2)."""
    w = max(0.05, float(width))
    two_w = 2.0 ** w
    return float(np.clip(np.sqrt(two_w) / (two_w - 1.0), 0.05, MAX_Q))


def biquad_coeffs(kind: str, f0: float, sample_rate: int, q: float = 0.707, gain_db: float = 0.0):
    """RBJ audio-EQ cookbook coefficients, normalised so a0 == 1."""
    nyq = 0.5 * sample_rate
    f0 = float(np.clip(f0, 10.0, 0.45 * sample_rate))
    q = float(np.clip(q, 0.05, MAX_Q))
    w0 = 2.0 * np.pi * f0 / sample_rate
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / (2.0 * q)
    A = 10.0 ** (gain_db / 40.0)

    if kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "notch":
        b = [1.0, -2 * cw, 1.0]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "emphasis":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif kind == "low_shelf":
        sq = 2.0 * np.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * cw + sq),
             2 * A * ((A - 1) - (A + 1) * cw),
             A * ((A + 1) - (A - 1) * cw - sq)]
        a = [(A + 1) + (A - 1) * cw + sq,
             -2 * ((A - 1) + (A + 1) * cw),
             (A + 1) + (A - 1) * cw - sq]
    elif kind == "high_shelf":
        sq = 2.0 * np.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + sq),
             -2 * A * ((A - 1) + (A + 1) * cw),
             A * ((A + 1) + (A - 1) * cw - sq)]
        a = [(A + 1) - (A - 1) * cw + sq,
             2 * ((A - 1) - (A + 1) * cw),
             (A + 1) - (A - 1) * cw - sq]
    else:
        raise ValueError(f"{kind!r} is not a biquad type")

    a0 = a[0]
    return [c / a0 for c in b], [1.0, a[1] / a0, a[2] / a0]


def _biquad_run(x: np.ndarray, b, a, state):
    """Direct form I. Returns (y, state) so blocks can be chained."""
    if _scipy_lfilter is not None:
        zi = np.array([
            b[1] * state[0] + b[2] * state[1] - a[1] * state[2] - a[2] * state[3],
            b[2] * state[0] - a[2] * state[2],
        ])
        y = _scipy_lfilter(b, a, x, zi=zi)[0]
        n = len(x)
        x1 = x[-1] if n >= 1 else state[0]
        x2 = x[-2] if n >= 2 else state[0] if n == 1 else state[1]
        y1 = y[-1] if n >= 1 else state[2]
        y2 = y[-2] if n >= 2 else state[2] if n == 1 else state[3]
        return y, [x1, x2, y1, y2]

    x1, x2, y1, y2 = state
    b0, b1, b2 = b
    _, a1, a2 = a
    y = np.empty_like(x)
    for i in range(x.shape[0]):
        xn = x[i]
        yn = b0 * xn + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        y[i] = yn
        x2, x1 = x1, xn
        y2, y1 = y1, yn
    return y, [x1, x2, y1, y2]


# ------------------------------------------------------------- time effects


def echo(x: np.ndarray, sample_rate: int, time: float, feedback: float, mix: float) -> np.ndarray:
    """Feedback delay, rendered as a finite sum of taps.

    Summing taps rather than running a recursive line keeps it vectorised and
    exactly reproducible; taps stop once they fall below -60 dB.
    """
    delay = max(1, int(round(time * sample_rate)))
    feedback = float(np.clip(feedback, 0.0, 0.95))
    mix = float(np.clip(mix, 0.0, 1.0))
    if mix <= 0.0 or x.size == 0:
        return x

    taps = 1 if feedback <= 1e-6 else min(64, int(np.ceil(np.log(0.001) / np.log(feedback))))
    out = np.zeros(x.size + delay * taps)
    out[: x.size] = x
    for k in range(1, taps + 1):
        start = delay * k
        out[start : start + x.size] += x * mix * (feedback ** (k - 1))
    return out


@lru_cache(maxsize=32)
def _impulse_response(sample_rate: int, size: float, damping: float) -> np.ndarray:
    """A synthetic reverb tail: decaying noise, rolled off by `damping`.

    Convolution with a generated impulse beats hand-tuned comb filters here —
    it is a few lines, it is deterministic, and there is no recursive loop to
    run one sample at a time in Python.
    """
    n = max(16, int(round(size * sample_rate)))
    rng = np.random.default_rng(20240817)  # fixed seed keeps renders reproducible
    tail = rng.normal(0.0, 1.0, n) * np.exp(-np.linspace(0.0, 6.9, n))

    damping = float(np.clip(damping, 0.0, 1.0))
    if damping > 1e-6:
        spectrum = np.fft.rfft(tail)
        freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
        cutoff = 18000.0 * (1.0 - damping) + 600.0 * damping
        spectrum /= np.sqrt(1.0 + (freqs / cutoff) ** 2)
        tail = np.fft.irfft(spectrum, n)

    energy = np.sqrt(np.sum(tail ** 2))
    return tail / energy if energy > 1e-12 else tail


def reverb(x: np.ndarray, sample_rate: int, size: float, damping: float,
           mix: float, predelay: float) -> np.ndarray:
    mix = float(np.clip(mix, 0.0, 1.0))
    if mix <= 0.0 or x.size == 0:
        return x
    ir = _impulse_response(sample_rate, float(np.clip(size, 0.05, 8.0)), damping)
    pre = max(0, int(round(max(0.0, predelay) * sample_rate)))

    n_out = x.size + ir.size - 1 + pre
    size_fft = 1 << int(np.ceil(np.log2(n_out)))
    wet = np.fft.irfft(np.fft.rfft(x, size_fft) * np.fft.rfft(ir, size_fft), size_fft)
    wet = wet[: x.size + ir.size - 1]

    out = np.zeros(n_out)
    out[: x.size] = x
    out[pre : pre + wet.size] += wet * mix
    return out


# --------------------------------------------------------------- restoration


def denoise(x: np.ndarray, sample_rate: int, amount: float, floor: float,
            window: int = 1024) -> np.ndarray:
    """Spectral subtraction.

    The noise profile is the quiet end of each frequency bin across the whole
    sample, so hiss and hum are estimated from the recording itself with no
    "select a silent region" step.
    """
    amount = float(np.clip(amount, 0.0, 1.0))
    if amount <= 0.0 or x.size < window * 2:
        return x

    hop = window // 4
    win = np.hanning(window)

    # Pad both ends so every real sample sits under a full set of overlapping
    # windows. Without this the overlap-add weights taper to nearly zero at the
    # edges and dividing by them amplifies the ends enormously — the first
    # version made a noisy tail 16x louder instead of quieter.
    pad = window
    padded = np.concatenate([np.zeros(pad), x, np.zeros(pad + window)])
    frames = 1 + (padded.size - window) // hop
    if frames < 4:
        return x

    idx = np.arange(window)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.fft.rfft(padded[idx] * win, axis=1)
    magnitude = np.abs(spec)

    # Quiet frames are noise, loud ones are signal. Noise magnitude in a bin is
    # Rayleigh-distributed, so a low percentile sits well under its mean —
    # subtracting the 10th percentile once removed only a quarter of the hiss.
    # Take the lower quartile and over-subtract, with `floor` guarding against
    # the musical-noise artefacts that over-subtraction otherwise causes.
    profile = np.percentile(magnitude, 25.0, axis=0)
    reduced = magnitude - (1.0 + 2.0 * amount) * profile[None, :]
    keep = float(np.clip(floor, 0.0, 1.0))
    reduced = np.maximum(reduced, magnitude * keep)

    with np.errstate(invalid="ignore", divide="ignore"):
        scale = np.where(magnitude > 1e-12, reduced / magnitude, 0.0)
    frames_out = np.fft.irfft(spec * scale, window, axis=1) * win

    out = np.zeros(padded.size)
    norm = np.zeros(padded.size)
    for i in range(frames):
        start = i * hop
        out[start : start + window] += frames_out[i]
        norm[start : start + window] += win ** 2
    norm = np.maximum(norm, 1e-6 * float(norm.max() or 1.0))
    out /= norm
    return out[pad : pad + x.size]


def sustain(x: np.ndarray, sample_rate: int, amount: float,
            attack: float, release: float) -> np.ndarray:
    """A sustainer: compress the loud part so the decay stays audible longer.

    The envelope follower is recursive, so it runs at control rate — one step
    per 64 samples — and the resulting gain is interpolated back up. That is
    hundreds of iterations per second of audio instead of tens of thousands.
    """
    amount = float(np.clip(amount, 0.0, 1.0))
    if amount <= 1e-6 or x.size == 0:
        return x

    block = 64
    blocks = max(1, x.size // block)
    trimmed = x[: blocks * block].reshape(blocks, block)
    level = np.sqrt(np.mean(trimmed ** 2, axis=1)) + 1e-9

    threshold = 10.0 ** ((-6.0 - 18.0 * amount) / 20.0)
    ratio = 1.0 + 5.0 * amount

    a_coeff = np.exp(-block / (max(attack, 1e-4) * sample_rate))
    r_coeff = np.exp(-block / (max(release, 1e-4) * sample_rate))
    envelope = np.empty(blocks)
    current = level[0]
    for i, value in enumerate(level):
        coeff = a_coeff if value > current else r_coeff
        current = coeff * current + (1.0 - coeff) * value
        envelope[i] = current

    over = np.maximum(envelope / threshold, 1.0)
    gain = over ** (1.0 / ratio - 1.0)

    # Makeup restores the level the compression took off the peaks, so it
    # MULTIPLIES the gain. Dividing by it (the first version) buried the whole
    # signal — a phrase peaking at 0.70 came out at 0.003. Capped so a heavy
    # setting cannot haul the noise floor up with it.
    makeup = min(threshold ** (1.0 / ratio - 1.0), 4.0)
    gain = np.clip(gain * makeup, 0.05, 8.0)

    centres = np.arange(blocks) * block + block / 2.0
    curve = np.interp(np.arange(x.size), centres, gain)
    return x * curve


def _resolve(node: FilterNode, name: str, u: np.ndarray, at: int) -> float:
    v = node.param(name)
    if isinstance(v, Trajectory):
        return float(v.value_at(float(u[min(at, len(u) - 1)])))
    return float(v)


# ------------------------------------------------------------------ pipeline


def apply_filter(x: np.ndarray, node: FilterNode, sample_rate: int, u: np.ndarray) -> np.ndarray:
    """Apply one filter node to a mono signal."""
    if not node.enabled:
        return x
    kind = node.type

    if kind == "bitcrush":
        bits = float(np.clip(_resolve(node, "bits", u, 0), 1.0, 24.0))
        levels = 2.0 ** (bits - 1)
        return np.round(x * levels) / levels

    if kind == "sr_reduce":
        target = float(np.clip(_resolve(node, "target_rate", u, 0), 100.0, sample_rate))
        hold = max(1, int(round(sample_rate / target)))
        if hold == 1:
            return x
        n = x.shape[0]
        kept = x[::hold]
        return np.repeat(kept, hold)[:n]

    if kind == "drive":
        amount = max(1e-3, _resolve(node, "amount", u, 0))
        return np.tanh(x * amount) / np.tanh(amount)

    if kind == "delay":
        return echo(x, sample_rate,
                    _resolve(node, "time", u, 0),
                    _resolve(node, "feedback", u, 0),
                    _resolve(node, "mix", u, 0))

    if kind == "reverb":
        return reverb(x, sample_rate,
                      _resolve(node, "size", u, 0),
                      _resolve(node, "damping", u, 0),
                      _resolve(node, "mix", u, 0),
                      _resolve(node, "predelay", u, 0))

    if kind == "denoise":
        return denoise(x, sample_rate,
                       _resolve(node, "amount", u, 0),
                       _resolve(node, "floor", u, 0))

    if kind == "sustain":
        return sustain(x, sample_rate,
                       _resolve(node, "amount", u, 0),
                       _resolve(node, "attack", u, 0),
                       _resolve(node, "release", u, 0))

    # Biquad family, processed in blocks so trajectory-valued parameters sweep.
    is_dynamic = any(isinstance(node.param(k), Trajectory) for k in FILTER_PARAMS.get(kind, {}))
    n = x.shape[0]
    if n == 0:
        return x
    out = np.empty_like(x)
    state = [0.0, 0.0, 0.0, 0.0]
    block = _BLOCK if is_dynamic else n

    for start in range(0, n, block):
        stop = min(n, start + block)
        mid = (start + stop) // 2
        if kind in ("lowpass", "highpass"):
            f0 = _resolve(node, "cutoff", u, mid)
            q = float(np.clip(_resolve(node, "resonance", u, mid), 0.05, MAX_Q))
            b, a = biquad_coeffs(kind, f0, sample_rate, q)
        elif kind in ("bandpass", "notch"):
            f0 = _resolve(node, "center", u, mid)
            q = q_from_octaves(_resolve(node, "width", u, mid))
            b, a = biquad_coeffs(kind, f0, sample_rate, q)
        elif kind == "emphasis":
            f0 = _resolve(node, "center", u, mid)
            q = q_from_octaves(_resolve(node, "width", u, mid))
            g = float(np.clip(_resolve(node, "amount", u, mid), -24.0, 24.0))
            b, a = biquad_coeffs(kind, f0, sample_rate, q, g)
        elif kind in ("low_shelf", "high_shelf"):
            f0 = _resolve(node, "corner", u, mid)
            g = float(np.clip(_resolve(node, "gain_db", u, mid), -24.0, 24.0))
            b, a = biquad_coeffs(kind, f0, sample_rate, 0.707, g)
        else:
            return x  # unknown type: pass through rather than destroy the render
        out[start:stop], state = _biquad_run(x[start:stop], b, a, state)
    return out


def brightness_chain(brightness: float, sample_rate: int) -> list[FilterNode]:
    """The Brightness macro (spec section 100.3). 0.5 is the identity point."""
    b = float(np.clip(brightness, 0.0, 1.0))
    if abs(b - 0.5) < 1e-9:
        return []
    f_min, f_max = 800.0, 16000.0
    cutoff = f_min * (f_max / f_min) ** b
    shelf = (b - 0.5) * 12.0
    return [
        FilterNode("lowpass", {"cutoff": cutoff, "resonance": 0.707}, True, "_brightness_lp"),
        FilterNode("high_shelf", {"corner": 4000.0, "gain_db": shelf}, True, "_brightness_hs"),
    ]


def apply_stack(
    x: np.ndarray, nodes: list[FilterNode], sample_rate: int, u: np.ndarray
) -> tuple[np.ndarray, list[str]]:
    warnings: list[str] = []
    y = x
    for node in nodes:
        before = y.shape[0]
        try:
            y = apply_filter(y, node, sample_rate, u)
            if y.shape[0] != before:
                # A delay or reverb tail made the signal longer; the normalised
                # time axis has to grow with it or later nodes read the wrong
                # point of a swept parameter.
                u = np.linspace(0.0, 1.0, y.shape[0]) if y.shape[0] > 1 else np.zeros(1)
        except Exception as exc:  # a bad filter must not lose the whole render
            warnings.append(f"filter {node.id!r} ({node.type}) failed: {exc}")
            continue
        if not np.all(np.isfinite(y)):
            warnings.append(
                f"filter {node.id!r} ({node.type}) produced non-finite output; node bypassed"
            )
            y = x if node is nodes[0] else np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    return y, warnings
