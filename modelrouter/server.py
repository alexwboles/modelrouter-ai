"""OpenAI-compatible proxy server (stdlib http.server only).

Endpoints:

- ``POST /v1/chat/completions`` — routed chat completion.
- ``GET /v1/models`` — the configured catalog plus virtual tier names.
- ``GET /healthz`` — liveness JSON.
- ``GET /`` — small status page (square corners, no rounded edges).

Streaming: ``stream: true`` on the Single tier is passed through to the
client chunk-by-chunk as real SSE. Cascade and Critique need the full
draft before they can judge it, so those tiers buffer internally and
then emit the final answer as one SSE chunk followed by ``[DONE]`` —
the client still receives a valid stream, just without incremental
tokens. This trade-off is documented in README.md and docs/ROUTING.md.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import backends
from .redact import redact_text
from .router import BudgetBlocked, Router

_STARTED = time.time()


def make_handler(router: Router, config, tracker):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # -- plumbing --------------------------------------------------
        def log_message(self, *args):  # keep stdout clean; see routing.jsonl
            pass

        def _send_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status, message):
            self._send_json(status, {
                "error": {
                    "message": redact_text(message),
                    "type": "modelrouter_error",
                    "code": status,
                }
            })

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                return None

        # -- routes ------------------------------------------------------
        def do_GET(self):
            if self.path in ("/v1/models", "/v1/models/"):
                data = [
                    {"id": name, "object": "model", "owned_by": "modelrouter"}
                    for name in config.virtual_model_names()
                ]
                self._send_json(200, {"object": "list", "data": data})
            elif self.path == "/healthz":
                self._send_json(200, {"status": "ok"})
            elif self.path in ("/", ""):
                self._send_status_page()
            else:
                self._send_error_json(404, f"unknown path {self.path}")

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self._send_error_json(404, f"unknown path {self.path}")
                return
            body = self._read_body()
            if body is None or not isinstance(body, dict):
                self._send_error_json(400, "request body must be a JSON object")
                return
            if not body.get("messages"):
                self._send_error_json(400, "request needs a non-empty 'messages' list")
                return
            project = self.headers.get("X-ModelRouter-Project")
            if project and not body.get("modelrouter_project"):
                body["modelrouter_project"] = project
            if body.get("stream"):
                self._handle_stream(body)
            else:
                self._handle_once(body)

        # -- handlers ------------------------------------------------------
        def _handle_once(self, body):
            try:
                response = router.handle(body)
            except BudgetBlocked as exc:
                self._send_error_json(429, str(exc))
            except backends.BackendError as exc:
                self._send_error_json(502, str(exc))
            except Exception as exc:  # never leak a traceback or a secret
                self._send_error_json(500, f"router error: {exc}")
            else:
                self._send_json(200, response)

        def _handle_stream(self, body):
            tier, effort, _project, model_override = router.select(body)
            if tier != "single":
                # Cascade/Critique judge a complete draft, so buffer and
                # emit the final answer as one SSE chunk + [DONE].
                try:
                    response = router.handle({**body, "stream": False})
                except BudgetBlocked as exc:
                    self._send_error_json(429, str(exc))
                    return
                except backends.BackendError as exc:
                    self._send_error_json(502, str(exc))
                    return
                except Exception as exc:
                    self._send_error_json(500, f"router error: {exc}")
                    return
                self._emit_sse_response(response)
                return
            # Single tier: true passthrough streaming.
            if model_override:
                model = config.require_model(model_override)
            else:
                model = config.model_for_role(tier, effort, "model")
            from .router import ROUTER_FIELDS
            forward = {k: v for k, v in body.items() if k not in ROUTER_FIELDS}
            forward["model"] = model.id
            forward.setdefault("stream_options", {"include_usage": True})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            usage, content_parts = {}, []
            try:
                for line in backends.stream(model, forward,
                                            config.policies.request_timeout):
                    self.wfile.write(line)
                    self.wfile.flush()
                    found = backends.parse_stream_usage(line)
                    if found:
                        usage = found
                    if line.startswith(b"data:"):
                        try:
                            delta = json.loads(line[5:].strip())["choices"][0]
                            delta = delta.get("delta", {}).get("content") or ""
                        except (ValueError, KeyError, IndexError):
                            delta = ""
                        content_parts.append(delta)
            except backends.BackendError as exc:
                # Stream already started: close it with an error chunk.
                err = json.dumps({"error": {"message": redact_text(str(exc))}})
                self.wfile.write(f"data: {err}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.close_connection = True
                return
            self.close_connection = True  # stream is complete; let readers see EOF
            # Log the streamed call like any other single-tier request.
            content = "".join(content_parts)
            attempt = {
                "model": model.id,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens") or 0,
                    "completion_tokens": usage.get("completion_tokens") or 0,
                },
                "cost_usd": 0.0,
                "content": content,
            }
            from .costs import cost_usd
            attempt["cost_usd"] = round(cost_usd(model, usage), 6)
            tracker.record(model.id, attempt["usage"], attempt["cost_usd"],
                           body.get("modelrouter_project"))
            entry = router._log_entry(
                body, tier, effort, body.get("modelrouter_project"), [attempt],
                None, attempt["usage"]["prompt_tokens"],
                attempt["usage"]["completion_tokens"], attempt["cost_usd"],
                "ok", time.monotonic(),
            )
            router._write_log(entry)

        def _emit_sse_response(self, response):
            chunk = {
                "id": response["id"],
                "object": "chat.completion.chunk",
                "created": response["created"],
                "model": response["model"],
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant",
                              "content": response["choices"][0]["message"]["content"]},
                    "finish_reason": "stop",
                }],
                "usage": response["usage"],
            }
            payload = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_status_page(self):
            from . import dashboard
            host = self.headers.get("Host") or "127.0.0.1:4000"
            html = dashboard.render_status_page(
                config, tracker, time.time() - _STARTED, router.log_path,
                f"http://{host}")
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(config, tracker, router, host: str, port: int):
    handler = make_handler(router, config, tracker)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
