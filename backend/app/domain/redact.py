"""Redact secrets and local filesystem paths before logs leave the server."""

from __future__ import annotations

import re

_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:tmp|home|opt|usr|var|workspace|app|Users|private)(?:/[^\s'\"<>]+)+"
)
_BEARER = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)(\S+)")
_API_KEY = re.compile(r"(?i)((?:api[_-]?key|token)\s*[:=]\s*)(\S+)")
_SK = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def redact(text: str, secrets: list[str] | None = None) -> str:
    output = text or ""
    for secret in secrets or []:
        if secret and len(secret) >= 4:
            output = output.replace(secret, "[redacted]")
    output = _PATH.sub("[path]", output)
    output = _BEARER.sub(r"\1[redacted]", output)
    output = _API_KEY.sub(r"\1[redacted]", output)
    output = _SK.sub("[redacted]", output)
    return output
