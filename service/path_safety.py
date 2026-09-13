"""
Path safety helpers.

User-controlled identifiers (upload filenames, trace ids, task ids, logger names, report
file names) all end up in filesystem paths. Naively joining them with ``base / value`` lets
``../`` or ``..\\`` escape the intended directory, which turns a read endpoint into an
arbitrary-file-read and an upload endpoint into an arbitrary-file-write. Route every such
join through :func:`safe_join` / :func:`sanitize_filename` instead.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

# Control characters (including NUL) are never valid in a filename segment.
_INVALID_CHARS = frozenset(chr(code) for code in range(32)) | {chr(127)}

# Keep generated names well below the common 255-byte limit of ext4/NTFS.
_MAX_NAME_LENGTH = 200

_FALLBACK_NAME = 'unnamed'


def sanitize_filename(name: PathLike) -> str:
    """
    Reduce a user supplied name to a single, harmless path segment.

    Both separators are handled explicitly so the behaviour does not depend on the host
    platform (on POSIX a backslash is a legal filename character, on Windows it separates
    directories). Absolute paths, ``..`` traversal and control characters are removed.

    Args:
        name: Raw name from a request (query parameter, multipart filename, id, ...)

    Returns:
        A name that is safe to append to a directory. Falls back to ``'unnamed'`` when the
        input is empty, ``.`` or ``..``.
    """
    raw = str(name or '').replace('\\', '/').split('/')[-1]
    cleaned = ''.join(char for char in raw if char not in _INVALID_CHARS).strip()
    if cleaned in ('', '.', '..'):
        return _FALLBACK_NAME
    return cleaned[:_MAX_NAME_LENGTH]


def safe_join(base: PathLike, *names: PathLike) -> Path:
    """
    Join ``names`` onto ``base`` and make sure the result stays inside ``base``.

    Every component is sanitized first, then the resolved result is verified to be inside
    the resolved base directory (defence in depth against symlinks and edge cases).

    Args:
        base: Directory the result must stay within
        names: One or more user supplied path segments

    Returns:
        The joined path (not resolved, so callers keep the original formatting)

    Raises:
        ValueError: If the resulting path would escape ``base``
    """
    base_path = Path(base)
    target = base_path.joinpath(*(sanitize_filename(name) for name in names))

    resolved_base = base_path.resolve()
    resolved_target = target.resolve()
    if resolved_target != resolved_base and not resolved_target.is_relative_to(resolved_base):
        raise ValueError(f'Unsafe path rejected: {target}')

    return target
