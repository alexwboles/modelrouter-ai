"""Fake OpenAI-compatible backends for tests (no network beyond localhost).

Each fake backend serves any model id and answers from a per-model
script queue, so a test can dictate exactly what the draft, the strong
model, or the critic says on each call. When a queue runs out, the
backend answers with a generic good response.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


class FakeBackend:
    def __init__(self):
        self.scripts = {}  # model id -> list of response dicts
        self.calls = []  # (model id, request body) in arrival order
        self._lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def script(self, model_id, responses):
        """Queue responses for a model. Each is one of:
        {"content": str, "usage": {...}} or {"status": int, "error": str}.
        """
        with self._lock:
            self.scripts.setdefault(model_id, []).extend(responses)

    def calls_for(self, model_id):
        with self._lock:
            return [body for model, body in self.calls if model == model_id]

    def _handler(self):
        backend = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send_json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                model_id = body.get("model", "")
                with backend._lock:
                    backend.calls.append((model_id, body))
                    queue = backend.scripts.get(model_id, [])
                    response = queue.pop(0) if queue else None
                if response is None:
                    response = {"content": "default good answer " * 4,
                                "usage": dict(DEFAULT_USAGE)}
                if "status" in response:
                    self._send_json(response["status"], {
                        "error": {"message": response.get("error", "boom"),
                                  "type": "fake_error"}})
                    return
                content = response.get("content", "")
                usage = response.get("usage", dict(DEFAULT_USAGE))
                if body.get("stream"):
                    # Minimal SSE: two content chunks, a usage chunk, DONE.
                    # Sent with a Content-Length so keep-alive clients can
                    # tell where the stream ends.
                    mid = max(1, len(content) // 2)
                    payload = b""
                    for piece in (content[:mid], content[mid:]):
                        chunk = {"choices": [{"delta": {"content": piece}}]}
                        payload += f"data: {json.dumps(chunk)}\n\n".encode()
                    usage_chunk = {"choices": [{}], "usage": usage}
                    payload += (
                        f"data: {json.dumps(usage_chunk)}\n\n"
                        "data: [DONE]\n\n"
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self._send_json(200, {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": model_id,
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": content},
                                 "finish_reason": "stop"}],
                    "usage": usage,
                })

        return Handler

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False
