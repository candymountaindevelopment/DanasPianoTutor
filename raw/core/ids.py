"""Asset identifiers.

Assets carry a stable UUID and a human-readable variable name. References
use the UUID so renaming and hot-swapping never break them (spec section 11).
"""

from __future__ import annotations

import re
import uuid
from typing import Iterable

IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def new_uid() -> str:
    return uuid.uuid4().hex


def is_valid_identifier(name: str) -> bool:
    return bool(IDENTIFIER_RE.match(name or ""))


def slugify(text: str, fallback: str = "asset") -> str:
    """Coerce arbitrary text into a safe variable name."""
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", (text or "").strip()).strip("_").lower()
    if not s:
        s = fallback
    if not s[0].isalpha() and s[0] != "_":
        s = "_" + s
    return s


def unique_name(base: str, taken: Iterable[str]) -> str:
    """Find the next free `base`, `base_1`, `base_2`, ..."""
    taken = set(taken)
    base = slugify(base)
    if base not in taken:
        return base
    i = 1
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"
