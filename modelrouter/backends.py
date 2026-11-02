"""OpenAI-compatible backend client (urllib only, no SDK).

Every backend — local Ollama ``/v1``, OpenAI, or any OpenAI-compatible
endpoint — is spoken to the same way: POST ``{endpoint}/chat/completions``
with an OpenAI-shaped body. Ollama's ``/v1`` surface accepts exactly this
shape (its API key, if any, is ignored for local models).
"""

import json
import urllib.error
import urllib.request

from .redact import redact_text


class BackendError(Exception):
    """An upstream failure, already redacted for safe reporting."""

    def __init__(self, status: int | None, message: str):
        self.status = status
        super().__init__(redact_text(message))
        self.message = redact_text(message)


def _request(model, body: dict, timeout: float) -> urllib.request.Request:
    url = model.endpoint + "/chat/completions"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    key = model.api_key()
    if key:
        req.add_header("Authorization", "Bearer " + key)
    return req


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:
        return f"HTTP {exc.code}"
    try:
        payload = json.loads(raw)
        message = payload.get("error", {}).get("message") or payload.get("error") or raw
        return f"HTTP {exc.code}: {message}"
    except (ValueError, AttributeError):
        return f"HTTP {exc.code}: {raw[:300]}"


def chat(model, body: dict, timeout: float):
    """One non-streaming chat completion.

    Returns ``(content, usage, raw_response)`` where usage is the
    upstream ``usage`` dict (possibly empty when the backend omits it).
    """
    req = _request(model, body, timeout)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BackendError(exc.code, _read_error(exc)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BackendError(None, f"unreachable backend {model.endpoint}: {exc}") from None
    except (ValueError, KeyError) as exc:
        raise BackendError(None, f"unparseable response from {model.endpoint}: {exc}") from None
    try:
        content = payload["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        raise BackendError(None, f"unexpected response shape from {model.endpoint}") from None
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return content, usage, payload


def stream(model, body: dict, timeout: float):
    """Streaming chat completion: yields raw SSE lines (bytes).

    The caller forwards lines to the client verbatim. The final chunk
    may carry a ``usage`` object when the backend was asked for it
    (``stream_options.include_usage``); this generator yields the lines
    unchanged and the caller parses usage out of them if present.
    """
    body = dict(body)
    body["stream"] = True
    req = _request(model, body, timeout)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise BackendError(exc.code, _read_error(exc)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BackendError(None, f"unreachable backend {model.endpoint}: {exc}") from None
    try:
        while True:
            line = resp.readline()
            if not line:
                break
            yield line
    finally:
        resp.close()


def parse_stream_usage(line: bytes):
    """Extract a usage dict from an SSE ``data:`` line, if it has one."""
    text = line.decode("utf-8", "replace").strip()
    if not text.startswith("data:"):
        return None
    payload = text[5:].strip()
    if payload in ("", "[DONE]"):
        return None
    try:
        usage = json.loads(payload).get("usage")
    except ValueError:
        return None
    return usage if isinstance(usage, dict) else None
