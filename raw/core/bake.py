"""Baking: turn rendered audio into a real file plus an AudioAsset.

Sample Lab and Sample FX work on files, so anything generated has to land on
disk before it can be sliced or processed (spec section 73). The file goes
beside the project as a relative path, which is the only arrangement that
survives moving the project folder.
"""

from __future__ import annotations

from pathlib import Path

from ..audio.buffer import AudioBuffer
from ..audio.io import clear_source_cache, write_wav
from .assets import AudioAsset
from .ids import slugify

GENERATED_DIR = "assets/generated"


class BakeError(RuntimeError):
    pass


def bake_path(project, name: str) -> Path:
    """A free path under the project for a generated file."""
    if project.path is None:
        raise BakeError(
            "Save the project first.\n\n"
            "Generated audio is written next to the project file so the "
            "reference stays relative and the folder can be moved."
        )
    folder = project.root / GENERATED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = slugify(name)
    candidate = folder / f"{stem}.wav"
    index = 1
    while candidate.exists():
        candidate = folder / f"{stem}_{index}.wav"
        index += 1
    return candidate


def bake_buffer(
    project,
    buffer: AudioBuffer,
    name: str,
    bit_depth: int = 16,
    meta: dict | None = None,
    tags: list[str] | None = None,
    description: str = "",
) -> AudioAsset:
    """Write `buffer` into the project and return an AudioAsset for it.

    The asset is not added to the project — the caller pushes an AddAsset so
    the whole thing stays one undoable step.
    """
    path = bake_path(project, name)
    write_wav(buffer, path, bit_depth)
    # The file is new, but a previous bake may have used this path and been
    # undone; make sure nothing stale is served from the decode cache.
    clear_source_cache()

    relative = path.relative_to(project.root).as_posix()
    return AudioAsset(
        name=slugify(name),
        source_path=relative,
        sample_rate=buffer.sample_rate,
        channels=buffer.channels,
        duration=buffer.duration,
        tags=list(tags or []),
        description=description,
        meta=dict(meta or {}),
    )
