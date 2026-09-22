"""Audio file I/O with a layered backend chain.

WAV is handled by the standard library so the app always works with no
dependencies at all. Compressed formats are decoded by whichever backend is
present, in this order:

    1. soundfile (libsndfile >= 1.1)  — MP3, OGG, FLAC, and much else
    2. FFmpeg on PATH, or a pip-installed imageio-ffmpeg binary

FFmpeg is therefore optional rather than required. If nothing can handle a
format, the error names the exact package to install rather than telling the
user to go and convert the file themselves.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import numpy as np

from .buffer import AudioBuffer

WAV_EXTENSIONS = (".wav",)
# Formats libsndfile handles that are worth offering in a file dialog.
SOUNDFILE_EXTENSIONS = (".mp3", ".ogg", ".flac", ".aiff", ".aif", ".au", ".w64", ".caf")
FFMPEG_EXTENSIONS = (".mp3", ".ogg", ".flac", ".m4a", ".aac", ".wma", ".opus")


# ----------------------------------------------------------------- backends


@lru_cache(maxsize=1)
def _soundfile():
    try:
        import soundfile  # noqa: PLC0415

        return soundfile
    except Exception:
        return None


@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    """FFmpeg on PATH, or the binary bundled with imageio-ffmpeg if installed."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def backend_summary() -> str:
    """Human-readable list of active decoders, for the status bar and About."""
    parts = ["wav (built in)"]
    sf = _soundfile()
    if sf is not None:
        parts.append(f"soundfile {sf.__version__} / libsndfile {sf.__libsndfile_version__}")
    if ffmpeg_path():
        parts.append("ffmpeg")
    return ", ".join(parts)


def readable_extensions() -> tuple[str, ...]:
    out = set(WAV_EXTENSIONS)
    if _soundfile() is not None:
        out.update(SOUNDFILE_EXTENSIONS)
    if ffmpeg_path():
        out.update(FFMPEG_EXTENSIONS)
    return tuple(sorted(out))


def writable_extensions() -> tuple[str, ...]:
    out = set(WAV_EXTENSIONS)
    if _soundfile() is not None:
        out.update((".ogg", ".flac", ".mp3"))
    if ffmpeg_path():
        out.update((".mp3", ".ogg"))
    return tuple(sorted(out))


def import_filter() -> str:
    """Qt file-dialog filter covering exactly what this install can read."""
    exts = readable_extensions()
    patterns = " ".join(f"*{e}" for e in exts)
    return f"Audio ({patterns});;WAV (*.wav);;All files (*)"


def export_filter() -> str:
    exts = writable_extensions()
    names = {".wav": "WAV", ".mp3": "MP3", ".ogg": "OGG", ".flac": "FLAC"}
    parts = [f"{names.get(e, e.upper().lstrip('.'))} (*{e})" for e in exts if e in names]
    return ";;".join(parts) or "WAV (*.wav)"


def _missing_backend_error(suffix: str) -> RuntimeError:
    return RuntimeError(
        f"No installed backend can handle {suffix} files.\n\n"
        "Fix it with either:\n"
        "    pip install soundfile        (recommended — adds MP3, OGG, FLAC)\n"
        "    pip install imageio-ffmpeg   (bundles an FFmpeg binary)\n\n"
        "or put FFmpeg on your PATH."
    )


# --------------------------------------------------------------------- read


def read_wav(path: str | Path) -> AudioBuffer:
    """Standard-library WAV reader. Handles 8/16/24/32-bit integer PCM."""
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())

    if width == 1:  # unsigned 8-bit
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float64) / 8388608.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {width * 8}-bit")

    if channels > 1:
        data = data.reshape(-1, channels)
    return AudioBuffer(data, rate)


def _read_soundfile(path: Path) -> AudioBuffer:
    sf = _soundfile()
    data, rate = sf.read(str(path), dtype="float64", always_2d=True)
    return AudioBuffer(data, int(rate))


def _read_ffmpeg(path: Path) -> AudioBuffer:
    ff = ffmpeg_path()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "decoded.wav"
        subprocess.run(
            [ff, "-v", "error", "-y", "-i", str(path), "-c:a", "pcm_s16le", str(out)],
            check=True,
            capture_output=True,
        )
        return read_wav(out)


_SOURCE_CACHE: "OrderedDict[tuple, AudioBuffer]" = OrderedDict()
_SOURCE_CACHE_BYTES = 0
SOURCE_CACHE_MAX_BYTES = 256 * 1024 * 1024


def _source_key(path: Path) -> tuple:
    stat = path.stat()
    return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def clear_source_cache() -> None:
    global _SOURCE_CACHE_BYTES
    _SOURCE_CACHE.clear()
    _SOURCE_CACHE_BYTES = 0


def source_cache_stats() -> str:
    return f"{len(_SOURCE_CACHE)} file(s) · {_SOURCE_CACHE_BYTES / (1024 * 1024):.1f} MB"


def read_audio_cached(path: str | Path) -> AudioBuffer:
    """Decode a file once and keep the result.

    Every slice of a sample re-reads its source, so without this a four-minute
    MP3 gets decoded again for each two-second slice on every cache miss. The
    buffer is shared, and every AudioBuffer operation returns a new array, so
    callers cannot corrupt it.

    Keyed on path plus mtime and size, so editing the file on disk invalidates.
    """
    global _SOURCE_CACHE_BYTES
    path = Path(path)
    try:
        key = _source_key(path)
    except OSError:
        return read_audio(path)

    cached = _SOURCE_CACHE.get(key)
    if cached is not None:
        _SOURCE_CACHE.move_to_end(key)
        return cached

    buf = read_audio(path)
    size = buf.samples.nbytes
    if size <= SOURCE_CACHE_MAX_BYTES:
        _SOURCE_CACHE[key] = buf
        _SOURCE_CACHE_BYTES += size
        while _SOURCE_CACHE_BYTES > SOURCE_CACHE_MAX_BYTES and len(_SOURCE_CACHE) > 1:
            _, dropped = _SOURCE_CACHE.popitem(last=False)
            _SOURCE_CACHE_BYTES -= dropped.samples.nbytes
    return buf


def read_audio(path: str | Path) -> AudioBuffer:
    """Read any format this install can handle, into the internal buffer."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such file: {path}")
    suffix = path.suffix.lower()
    errors: list[str] = []

    if suffix in WAV_EXTENSIONS:
        try:
            return read_wav(path)
        except Exception as exc:
            # Float WAV and WAVE_FORMAT_EXTENSIBLE defeat the stdlib reader;
            # libsndfile reads both, so fall through rather than give up.
            errors.append(f"built-in wav reader: {exc}")

    if _soundfile() is not None:
        try:
            return _read_soundfile(path)
        except Exception as exc:
            errors.append(f"soundfile: {exc}")

    if ffmpeg_path():
        try:
            return _read_ffmpeg(path)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            errors.append(f"ffmpeg: {detail[-1] if detail else exc}")
        except Exception as exc:
            errors.append(f"ffmpeg: {exc}")

    if not errors:
        raise _missing_backend_error(suffix)
    raise RuntimeError(f"could not read {path.name}:\n  " + "\n  ".join(errors))


# -------------------------------------------------------------------- write


def write_wav(buffer: AudioBuffer, path: str | Path, bit_depth: int = 16) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(buffer.samples, -1.0, 1.0)

    if bit_depth == 16:
        raw = np.round(x * 32767.0).astype("<i2").tobytes()
        width = 2
    elif bit_depth == 24:
        ints = np.round(x * 8388607.0).astype("<i4").ravel()
        b = np.empty((ints.shape[0], 3), dtype=np.uint8)
        b[:, 0] = ints & 0xFF
        b[:, 1] = (ints >> 8) & 0xFF
        b[:, 2] = (ints >> 16) & 0xFF
        raw = b.tobytes()
        width = 3
    elif bit_depth == 32:
        raw = np.round(x * 2147483647.0).astype("<i4").tobytes()
        width = 4
    else:
        raise ValueError(f"unsupported bit depth: {bit_depth}")

    with wave.open(str(path), "wb") as w:
        w.setnchannels(buffer.channels)
        w.setsampwidth(width)
        w.setframerate(buffer.sample_rate)
        w.writeframes(raw)
    return path


def _bitrate_kbps(quality: str | int) -> int:
    if isinstance(quality, (int, float)):
        return int(quality)
    digits = "".join(ch for ch in str(quality) if ch.isdigit())
    return int(digits) if digits else 192


def _write_ffmpeg(buffer: AudioBuffer, path: Path, quality: str) -> Path:
    ff = ffmpeg_path()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "source.wav"
        write_wav(buffer, src, 24)
        if path.suffix.lower() == ".mp3":
            codec = ["-c:a", "libmp3lame", "-b:a", f"{_bitrate_kbps(quality)}k"]
        elif path.suffix.lower() == ".ogg":
            codec = ["-c:a", "libvorbis", "-q:a", "5"]
        else:
            codec = []
        subprocess.run(
            [ff, "-v", "error", "-y", "-i", str(src), *codec, str(path)],
            check=True,
            capture_output=True,
        )
    return path


def write_audio(
    buffer: AudioBuffer, path: str | Path, bit_depth: int = 16, quality: str = "192k"
) -> Path:
    """Write WAV directly; compressed formats through the best available backend."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in WAV_EXTENSIONS:
        return write_wav(buffer, path, bit_depth)

    path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    # FFmpeg first for MP3 because it honours an exact bitrate; libsndfile does not.
    if suffix == ".mp3" and ffmpeg_path():
        try:
            return _write_ffmpeg(buffer, path, quality)
        except Exception as exc:
            errors.append(f"ffmpeg: {exc}")

    sf = _soundfile()
    if sf is not None:
        try:
            sf.write(str(path), np.clip(buffer.samples, -1.0, 1.0), buffer.sample_rate)
            return path
        except Exception as exc:
            errors.append(f"soundfile: {exc}")

    if ffmpeg_path():
        try:
            return _write_ffmpeg(buffer, path, quality)
        except Exception as exc:
            errors.append(f"ffmpeg: {exc}")

    if not errors:
        raise _missing_backend_error(suffix)
    raise RuntimeError(f"could not write {path.name}:\n  " + "\n  ".join(errors))
