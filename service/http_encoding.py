"""
HTTP response encoding helpers.

Centralises the logic for ensuring responses that carry JSON or text without an
explicit charset declare ``charset=utf-8``. Kept as a tiny pure module so it can be
unit-tested without importing the (dependency-heavy) FastAPI application.
"""


def with_utf8_charset(content_type: str) -> str:
    """
    Return a ``content-type`` header value that declares UTF-8 for JSON / text.

    Behaviour:
    - If the header already contains ``charset`` (case-insensitive) it is returned
      unchanged.
    - ``application/json`` and ``text/*`` media types get ``; charset=utf-8``.
      (``image/*``, ``application/octet-stream``, custom types, etc. are untouched.)
    - Otherwise the original value is returned unchanged.

    Args:
        content_type: raw ``Content-Type`` header value.

    Returns:
        Possibly amended ``Content-Type`` header value.
    """
    if not content_type:
        return content_type

    if 'charset' in content_type.lower():
        return content_type

    media_type = content_type.split(';')[0].strip().lower()
    if not (media_type.startswith('application/json') or media_type.startswith('text/')):
        return content_type

    # Canonical separator is '; ' (space after the semi-colon), matching the
    # conventional form used across the codebase (e.g. "application/json; charset=utf-8").
    contents = content_type.strip()
    if contents[-1] in (';', ' '):
        contents = contents.rstrip()
    return f"{contents}; charset=utf-8"

