"""Secret redaction and prompt hashing.

Two hard rules, enforced here and nowhere else:

1. API keys live ONLY in environment variables. They are never written to
   a config file, a log line, or an error message.
2. The routing log stores a SHA-256 hash of the prompt, never the prompt
   text itself.
"""

import hashlib
import json
import re

_SENSITIVE_KEY = re.compile(r"(key|token|secret|password|auth|bearer|credential)", re.I)
_BEARER = re.compile(r"(Bearer\s+)[^\s\"']+", re.I)
_KV_SECRET = re.compile(
    r"((?:api[_-]?key|access[_-]?token|secret|password|authorization)\s*[:=]\s*)"
    r"[^\s\"',}]+",
    re.I,
)
_SK_LIKE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b")

REDACTED = "***REDACTED***"


def redact_text(text: str) -> str:
    """Scrub bearer tokens and key-like values out of a free-text string."""
    if not isinstance(text, str):
        text = str(text)
    text = _BEARER.sub(r"\1" + REDACTED, text)
    text = _KV_SECRET.sub(r"\1" + REDACTED, text)
    text = _SK_LIKE.sub(REDACTED, text)
    return text


def redact_obj(value):
    """Recursively redact sensitive keys in dicts/lists."""
    if isinstance(value, dict):
        return {
            (k if not _SENSITIVE_KEY.search(str(k)) else k): (
                REDACTED if _SENSITIVE_KEY.search(str(k)) else redact_obj(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def prompt_hash(messages) -> str:
    """SHA-256 over the canonical JSON of the message list.

    The hash lets an operator correlate a log line with a request they
    already hold, without the log ever containing prompt text.
    """
    canonical = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
