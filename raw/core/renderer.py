"""Render service: asset -> AudioBuffer, with a parameter-hash cache.

Overrides that only scale the output (volume, pan) are applied to the cached
base buffer instead of forcing a re-render (spec section 102.2).
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict

import numpy as np

from .. import __version__
from ..audio.buffer import AudioBuffer
from ..audio.io import read_audio_cached
from ..synth import engine
from ..synth.filters import apply_stack
from ..synth.timestructure import TimeStructure
from .assets import Asset, AudioAsset, InstrumentAsset, PatternAsset, SynthAsset
from .overrides import POST_PROCESS_PARAMS, resolve_duration, structural_subset


def cache_id(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((canonical + "|" + __version__).encode("utf-8")).hexdigest()


def dependency_keys(asset: Asset, project) -> dict:
    """Render keys of everything `asset` needs, transitively.

    A pattern's own dict says nothing about the instruments it points at, so
    without this an edit to an instrument leaves cached pattern renders stale —
    which is exactly the "assign the sound later" workflow. Cycles are
    tolerated: a slot could bind a pattern back to itself.
    """
    out: dict = {}
    seen = {asset.uid}
    stack = [asset]
    while stack:
        current = stack.pop()
        for uid in current.dependencies(project):
            if uid in seen:
                continue
            seen.add(uid)
            dep = project.assets.get(uid)
            out[uid] = dep.render_key() if dep is not None else None
            if dep is not None:
                stack.append(dep)
    return out


class RenderCache:
    def __init__(self, max_bytes: int = 256 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, AudioBuffer] = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> AudioBuffer | None:
        buf = self._entries.get(key)
        if buf is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return buf

    def put(self, key: str, buffer: AudioBuffer) -> None:
        size = buffer.samples.nbytes
        if key in self._entries:
            self._bytes -= self._entries[key].samples.nbytes
        self._entries[key] = buffer
        self._entries.move_to_end(key)
        self._bytes += size
        while self._bytes > self.max_bytes and len(self._entries) > 1:
            _, dropped = self._entries.popitem(last=False)
            self._bytes -= dropped.samples.nbytes

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def invalidate_prefix(self, prefix: str) -> None:
        for key in [k for k in self._entries if k.startswith(prefix)]:
            self._bytes -= self._entries.pop(key).samples.nbytes

    @property
    def stats(self) -> str:
        mb = self._bytes / (1024 * 1024)
        return f"{len(self._entries)} entries · {mb:.1f} MB · {self.hits} hits / {self.misses} misses"


class Renderer:
    def __init__(self, cache: RenderCache | None = None) -> None:
        self.cache = cache or RenderCache()
        self.last_warnings: list[str] = []

    def render_asset(
        self,
        asset: Asset,
        project,
        overrides: dict | None = None,
        preview: bool = False,
        use_cache: bool = True,
    ) -> AudioBuffer:
        ov = dict(overrides or {})
        post = {k: v for k, v in ov.items() if k in POST_PROCESS_PARAMS}
        structural = structural_subset(ov)
        sr = project.settings.sample_rate

        key = cache_id(
            {
                "uid": asset.uid,
                "asset": asset.render_key(),
                "deps": dependency_keys(asset, project),
                "ov": structural,
                "sr": sr,
                "tempo": project.settings.tempo,
                "preview": preview,
            }
        )
        buf = self.cache.get(key) if use_cache else None
        if buf is None:
            buf, warnings = self._render_base(asset, project, structural, preview)
            self.last_warnings = warnings
            if use_cache:
                self.cache.put(key, buf)
        else:
            self.last_warnings = []

        if "volume_db" in post:
            buf = buf.gain_db(float(post["volume_db"]))
        if "pan" in post and abs(float(post["pan"])) > 1e-6:
            buf = buf.panned(float(post["pan"]))
        return buf

    def render_placeholder(
        self, slot_name: str, project, overrides: dict | None = None, preview: bool = False
    ) -> AudioBuffer:
        """The stand-in blip for an unbound slot (spec Part II, slots)."""
        from .slots import placeholder_params

        ov = dict(overrides or {})
        post = {k: v for k, v in ov.items() if k in POST_PROCESS_PARAMS}
        structural = structural_subset(ov)
        sr = project.settings.sample_rate

        key = cache_id(
            {
                "placeholder": slot_name,
                "ov": structural,
                "sr": sr,
                "tempo": project.settings.tempo,
                "preview": preview,
            }
        )
        buf = self.cache.get(key)
        if buf is None:
            result = engine.render(
                placeholder_params(slot_name),
                sample_rate=sr,
                overrides=structural,
                tempo=project.settings.tempo,
                preview=preview,
            )
            buf = result.buffer
            self.cache.put(key, buf)

        if "volume_db" in post:
            buf = buf.gain_db(float(post["volume_db"]))
        if "pan" in post and abs(float(post["pan"])) > 1e-6:
            buf = buf.panned(float(post["pan"]))
        return buf

    # ------------------------------------------------------------- internals

    def _render_base(self, asset, project, overrides: dict, preview: bool):
        sr = project.settings.sample_rate
        if isinstance(asset, (SynthAsset, InstrumentAsset)):
            result = engine.render(
                asset.params,
                sample_rate=sr,
                overrides=overrides,
                tempo=project.settings.tempo,
                preview=preview,
            )
            return result.buffer, result.warnings
        if isinstance(asset, AudioAsset):
            return self._render_sample(asset, project, overrides)
        if isinstance(asset, PatternAsset):
            from ..patterns.renderer import render_pattern

            # Imported here to keep the core -> patterns dependency one-way.
            # Overrides on a pattern instance apply to every event inside it,
            # so a whole pattern can be transposed or dimmed from one place.
            return render_pattern(asset, project, self, overrides=overrides, preview=preview)
        return AudioBuffer.silence(0.25, sr), [f"{asset.type_name} cannot be rendered yet"]

    def _render_sample(self, asset: AudioAsset, project, overrides: dict):
        warnings: list[str] = []
        path = project.resolve_asset_path(asset.source_path) if asset.source_path else None
        if path is None or not path.exists():
            asset.missing = True
            return AudioBuffer.silence(0.25, project.settings.sample_rate), [
                f"source missing: {asset.source_path}"
            ]
        asset.missing = False
        buf = read_audio_cached(path)

        if asset.is_slice:
            start = max(0.0, min(float(asset.trim_start), buf.duration))
            end = buf.duration if asset.trim_length is None else start + float(asset.trim_length)
            end = min(max(end, start), buf.duration)
            if end - start < 1.0 / max(buf.sample_rate, 1):
                warnings.append(
                    f"slice bounds {start:.3f}s..{end:.3f}s fall outside the source; using the whole file"
                )
            else:
                buf = buf.slice_seconds(start, end)

        buf, fx_warnings = apply_sample_effects(asset, buf)
        warnings += fx_warnings

        if "pitch_offset" in overrides:
            ratio = 2.0 ** (float(overrides["pitch_offset"]) / 12.0)
            buf = AudioBuffer(buf.samples, int(round(buf.sample_rate * ratio))).resampled(
                buf.sample_rate
            )

        if "duration" in overrides:
            target = resolve_duration(overrides["duration"], buf.duration, project.settings.tempo)
        elif asset.fit_bars:
            target = float(asset.fit_bars) * bar_seconds(project)
        else:
            target = buf.duration

        if abs(target - buf.duration) > 1e-6:
            buf, w = self._stretch_sample(buf, asset, target)
            warnings += w
        return buf, warnings

    def _stretch_sample(self, buf: AudioBuffer, asset: AudioAsset, target: float):
        """Approximate stretching for imported audio (spec section 99)."""
        method = asset.stretch_method or "tape"
        ratio = target / max(buf.duration, 1e-9)
        warnings = [f"imported audio stretched with '{method}' — this is approximate"]

        if method == "tape":
            # Resample straight to the target frame count. Going via an integer
            # sample rate both inverted the ratio (a 2.220s loop asked for
            # 2.000s produced 2.464s) and landed a sample off the length.
            return buf.resampled_frames(round(target * buf.sample_rate)), warnings

        ts = asset.time_structure
        if ts is not None and method in ("loop_body", "wsola"):
            # Copy rigid regions verbatim; process only the elastic remainder.
            tmap, w = ts.build_map(target)
            warnings += w
            parts: list[AudioBuffer] = []
            for i, region in enumerate(ts.regions):
                nom_a = tmap.nominal_bounds[i]
                nom_b = tmap.nominal_bounds[i + 1]
                out_len = tmap.output_bounds[i + 1] - tmap.output_bounds[i]
                seg = buf.slice_seconds(nom_a, nom_b)
                if abs(out_len - (nom_b - nom_a)) < 1e-9 or seg.num_frames == 0:
                    parts.append(seg)
                else:
                    parts.append(_wsola(seg, out_len))
            return _crossfade_concat(parts), warnings

        return _wsola(buf, target), warnings


def bar_seconds(project) -> float:
    """One bar at the project tempo and time signature."""
    beats_per_bar = project.settings.time_signature[0]
    return (60.0 / max(project.settings.tempo, 1e-6)) * beats_per_bar


def bars_for(duration: float, project) -> float:
    """How many bars a length occupies. 2.22s at 120 BPM 4/4 is 1.11 bars."""
    return duration / max(bar_seconds(project), 1e-9)


def implied_tempo(duration: float, bars: float, project) -> float:
    """The tempo at which `duration` would be exactly `bars` bars."""
    beats_per_bar = project.settings.time_signature[0]
    return (bars * beats_per_bar * 60.0) / max(duration, 1e-9)


def apply_sample_effects(asset: AudioAsset, buf: AudioBuffer) -> tuple[AudioBuffer, list[str]]:
    """The sample FX chain, in a fixed order.

        reverse -> fades -> gain -> filters -> normalize

    Fixed rather than a free node graph because this is the order that makes
    musical sense: fading a reversed sample fades what you actually hear, and
    normalise comes last so the number on the control is the peak you get.
    Filters change level substantially — a bandpass plus bitcrush took a
    normalised 0.90 down to 0.61 when normalise ran before them.
    """
    warnings: list[str] = []
    if buf.num_frames == 0:
        return buf, warnings

    if asset.reverse:
        buf = buf.reversed()
    if asset.fade_in > 0.0 or asset.fade_out > 0.0:
        buf = buf.faded(asset.fade_in, asset.fade_out)
    if abs(asset.gain_db) > 1e-9:
        buf = buf.gain_db(asset.gain_db)

    if asset.filters:
        u = np.linspace(0.0, 1.0, buf.num_frames)
        columns = []
        for channel in range(buf.channels):
            processed, w = apply_stack(
                buf.samples[:, channel], asset.filters, buf.sample_rate, u
            )
            columns.append(processed)
            for message in w:
                if message not in warnings:
                    warnings.append(message)
        buf = AudioBuffer(np.stack(columns, axis=1), buf.sample_rate)

    if asset.normalize is not None:
        buf = buf.normalized(float(asset.normalize))
    return buf, warnings


def _crossfade_concat(parts: list[AudioBuffer], fade_frames: int = 64) -> AudioBuffer:
    parts = [p for p in parts if p.num_frames > 0]
    if not parts:
        return AudioBuffer.silence(0.0)
    sr = parts[0].sample_rate
    out = parts[0].samples
    for nxt in parts[1:]:
        n = min(fade_frames, out.shape[0], nxt.num_frames)
        if n <= 0:
            out = np.concatenate([out, nxt.samples], axis=0)
            continue
        ramp = np.linspace(0.0, 1.0, n)[:, None]
        head = out[-n:] * np.cos(ramp * np.pi / 2) + nxt.samples[:n] * np.sin(ramp * np.pi / 2)
        out = np.concatenate([out[:-n], head, nxt.samples[n:]], axis=0)
    return AudioBuffer(out, sr)


def _wsola(buf: AudioBuffer, target_duration: float, window_ms: float = 30.0) -> AudioBuffer:
    """Overlap-add time stretch that preserves pitch. Approximate by nature."""
    sr = buf.sample_rate
    x = buf.samples.mean(axis=1)
    n_in = x.shape[0]
    n_out = max(1, int(round(target_duration * sr)))
    if n_in < 8:
        return AudioBuffer(np.zeros((n_out, 1)), sr)

    win = max(64, int(window_ms * sr / 1000.0))
    win = min(win, n_in)
    hop_out = win // 2
    ratio = n_in / n_out
    hop_in = max(1, int(round(hop_out * ratio)))
    window = np.hanning(win)

    out = np.zeros(n_out + win)
    norm = np.zeros(n_out + win)
    pos_in = 0
    pos_out = 0
    while pos_out < n_out:
        seg = x[pos_in : pos_in + win]
        if seg.shape[0] < win:
            seg = np.pad(seg, (0, win - seg.shape[0]))
        out[pos_out : pos_out + win] += seg * window
        norm[pos_out : pos_out + win] += window
        pos_out += hop_out
        pos_in = min(n_in - 1, pos_in + hop_in)
    norm[norm < 1e-9] = 1.0
    return AudioBuffer((out / norm)[:n_out], sr)
