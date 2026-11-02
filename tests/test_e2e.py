"""End-to-end tests: real proxy server + fake Ollama + fake OpenAI
backends, all on localhost. Covers every tier flow, streaming, cost
report accuracy, budget stop, and log redaction."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakebackend import DEFAULT_USAGE, FakeBackend
from modelrouter import server
from modelrouter.config import load_config
from modelrouter.costs import CostTracker
from modelrouter.router import Router

GOOD_DRAFT = "Here is the answer:\n```python\ndef add(a, b):\n    return a + b\n```\n"
LONG_PLAIN = "A long, detailed explanation with no code block. " * 4
CODE_PROMPT = [{"role": "user", "content": "write a function that adds two numbers"}]


def _config_text(ollama_url, openai_url, daily_usd=1000.0):
    return f"""models:
  - id: local-fast
    endpoint: {ollama_url}
    key_env: ""
    input_price: 0.0
    output_price: 0.0
    context_window: 32768
  - id: api-cheap
    endpoint: {openai_url}
    key_env: ""
    input_price: 1.00
    output_price: 2.00
    context_window: 128000
  - id: api-strong
    endpoint: {openai_url}
    key_env: ""
    input_price: 10.00
    output_price: 20.00
    context_window: 200000
tiers:
  single: {{model: local-fast}}
  cascade: {{draft: local-fast, strong: api-strong, min_length: 40}}
  critique: {{primary: api-cheap, critic: api-strong, threshold: 7, max_rounds: 2}}
effort:
  default: medium
  low: {{}}
  medium: {{}}
  high: {{}}
policies:
  default_tier: cascade
  request_timeout: 10
  budget: {{daily_usd: {daily_usd}, warn_fraction: 0.8, stop_fraction: 1.0}}
"""


class ProxyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ollama = FakeBackend()
        cls.openai = FakeBackend()
        cls.ollama.__enter__()
        cls.openai.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ollama.__exit__()
        cls.openai.__exit__()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ollama.scripts.clear()
        self.ollama.calls.clear()
        self.openai.scripts.clear()
        self.openai.calls.clear()
        self.cfg_path = os.path.join(self.tmp.name, "modelrouter.yaml")
        with open(self.cfg_path, "w") as fh:
            fh.write(_config_text(self.ollama.url, self.openai.url))
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.proxy, self.port = self._start_proxy(self.cfg_path, self.data_dir)

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.tmp.cleanup()

    def _start_proxy(self, cfg_path, data_dir):
        config = load_config(cfg_path)
        tracker = CostTracker(os.path.join(data_dir, "costs.json"))
        router = Router(config, tracker, os.path.join(data_dir, "routing.jsonl"))
        httpd = server.serve(config, tracker, router, "127.0.0.1", 0)
        import threading
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, httpd.server_address[1]

    # -- helpers ------------------------------------------------------
    def post(self, body, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=15) as resp:
            return resp.status, json.loads(resp.read())

    def log_entries(self):
        path = os.path.join(self.data_dir, "routing.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    # -- catalog --------------------------------------------------------
    def test_models_endpoint_lists_models_and_tiers(self):
        status, payload = self.get("/v1/models")
        self.assertEqual(status, 200)
        ids = {item["id"] for item in payload["data"]}
        self.assertIn("local-fast", ids)
        self.assertIn("api-strong", ids)
        self.assertIn("cascade", ids)
        self.assertIn("cascade:high", ids)

    # -- single -----------------------------------------------------------
    def test_single_direct_model_and_cost_math(self):
        self.ollama.script("local-fast", [])
        status, payload = self.post({
            "model": "api-cheap", "messages": CODE_PROMPT,
        })
        # api-cheap is a model id -> Single tier to that model.
        self.assertEqual(status, 200)
        self.assertEqual(payload["modelrouter"]["tier"], "single")
        self.assertEqual(payload["modelrouter"]["models_tried"], ["api-cheap"])
        usage = {"prompt_tokens": 100, "completion_tokens": 50}
        self.assertEqual(payload["usage"]["prompt_tokens"], 100)
        tracker = CostTracker(os.path.join(self.data_dir, "costs.json"))
        # 100/1M * $1.00 + 50/1M * $2.00 = $0.0002
        self.assertAlmostEqual(tracker.spent_today(), 0.0002, places=6)

    # -- cascade ----------------------------------------------------------
    def test_cascade_accepts_good_draft(self):
        self.ollama.script("local-fast", [{"content": GOOD_DRAFT}])
        status, payload = self.post({"model": "cascade", "messages": CODE_PROMPT})
        self.assertEqual(status, 200)
        self.assertIsNone(payload["modelrouter"]["escalation_reason"])
        self.assertEqual(self.openai.calls_for("api-strong"), [])

    def test_cascade_escalates_on_empty_draft(self):
        self.ollama.script("local-fast", [{"content": ""}])
        self.openai.script("api-strong", [{"content": GOOD_DRAFT}])
        status, payload = self.post({"model": "cascade", "messages": CODE_PROMPT})
        self.assertEqual(status, 200)
        self.assertEqual(payload["modelrouter"]["escalation_reason"], "empty_output")
        self.assertEqual(payload["modelrouter"]["models_tried"],
                         ["local-fast", "api-strong"])
        self.assertIn("def add", payload["choices"][0]["message"]["content"])

    def test_cascade_escalates_on_refusal(self):
        self.ollama.script("local-fast", [
            {"content": "I'm sorry, but I can't help with that request at all."}])
        self.openai.script("api-strong", [{"content": GOOD_DRAFT}])
        _s, payload = self.post({"model": "cascade", "messages": CODE_PROMPT})
        reason = payload["modelrouter"]["escalation_reason"]
        self.assertIn("refusal", reason)
        self.assertIn("missing_code_block", reason)  # gates join with "+"

    def test_cascade_escalates_on_missing_code_block(self):
        self.ollama.script("local-fast", [{"content": LONG_PLAIN}])
        self.openai.script("api-strong", [{"content": GOOD_DRAFT}])
        _s, payload = self.post({"model": "cascade", "messages": CODE_PROMPT})
        self.assertEqual(payload["modelrouter"]["escalation_reason"],
                         "missing_code_block")

    def test_cascade_escalates_on_upstream_error(self):
        self.ollama.script("local-fast", [{"status": 500, "error": "ollama exploded"}])
        self.openai.script("api-strong", [{"content": GOOD_DRAFT}])
        _s, payload = self.post({"model": "cascade", "messages": CODE_PROMPT})
        self.assertEqual(payload["modelrouter"]["escalation_reason"],
                         "upstream_error:500")

    def test_cascade_both_fail_returns_502_without_secret(self):
        secret = "sk-faketestsecret999"
        self.ollama.script("local-fast", [{"status": 500, "error": "down"}])
        self.openai.script("api-strong",
                           [{"status": 500, "error": f"key {secret} leaked"}])
        status, payload = self.post({"model": "cascade", "messages": CODE_PROMPT})
        self.assertEqual(status, 502)
        self.assertNotIn(secret, json.dumps(payload))

    # -- critique ---------------------------------------------------------
    def _critique_setup(self, critic_scores, drafts=None):
        drafts = drafts or [GOOD_DRAFT]
        self.openai.script("api-cheap", [{"content": d} for d in drafts])
        self.openai.script("api-strong", [
            {"content": json.dumps({"score": s, "notes": f"score {s}"})}
            for s in critic_scores
        ])

    def test_critique_accepts_above_threshold(self):
        self._critique_setup([9])
        _s, payload = self.post({"model": "critique", "messages": CODE_PROMPT})
        self.assertEqual(payload["choices"][0]["message"]["content"], GOOD_DRAFT)
        self.assertEqual(len(self.openai.calls_for("api-cheap")), 1)
        self.assertEqual(len(self.openai.calls_for("api-strong")), 1)

    def test_critique_revises_then_accepts(self):
        revised = GOOD_DRAFT + "# revised\n"
        self._critique_setup([5, 8], drafts=[GOOD_DRAFT, revised])
        _s, payload = self.post({"model": "critique", "messages": CODE_PROMPT})
        self.assertEqual(payload["choices"][0]["message"]["content"], revised)
        self.assertEqual(len(self.openai.calls_for("api-cheap")), 2)
        self.assertEqual(len(self.openai.calls_for("api-strong")), 2)

    def test_critique_round_cap_returns_best(self):
        drafts = [f"draft-{i} " + GOOD_DRAFT for i in range(3)]
        self._critique_setup([3, 3, 3], drafts=drafts)
        _s, payload = self.post({"model": "critique", "messages": CODE_PROMPT})
        # 1 initial + 2 revision rounds = 3 primary calls, 3 critic calls.
        self.assertEqual(len(self.openai.calls_for("api-cheap")), 3)
        self.assertEqual(len(self.openai.calls_for("api-strong")), 3)
        self.assertEqual(payload["choices"][0]["message"]["content"], drafts[0])
        entry = self.log_entries()[-1]
        self.assertEqual(entry["escalation_reason"], "below_threshold_after_2_rounds")

    # -- streaming ---------------------------------------------------------
    def test_streaming_single_passes_through(self):
        self.ollama.script("local-fast", [{"content": GOOD_DRAFT}])
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps({"model": "local-fast", "messages": CODE_PROMPT,
                             "stream": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
        self.assertIn("data: [DONE]", raw)
        content = ""
        for line in raw.splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                delta = json.loads(line[6:])["choices"][0].get("delta", {})
                content += delta.get("content", "")
        self.assertEqual(content, GOOD_DRAFT)

    def test_streaming_cascade_emits_buffered_stream(self):
        self.ollama.script("local-fast", [{"content": GOOD_DRAFT}])
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps({"model": "cascade", "messages": CODE_PROMPT,
                             "stream": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
        self.assertIn("data: [DONE]", raw)
        self.assertIn("def add", raw)

    # -- logging, costs, budget ---------------------------------------------
    def test_routing_log_has_hash_not_prompt_text(self):
        marker = "zebra-prompt-marker-12345"
        self.ollama.script("local-fast", [{"content": GOOD_DRAFT}])
        self.post({"model": "cascade",
                   "messages": [{"role": "user", "content": marker}]})
        entries = self.log_entries()
        self.assertEqual(len(entries), 1)
        blob = json.dumps(entries[0])
        self.assertNotIn(marker, blob)
        self.assertEqual(len(entries[0]["prompt_hash"]), 64)
        self.assertEqual(entries[0]["tier"], "cascade")

    def test_costs_cli_report_matches_usage(self):
        self.openai.script("api-cheap", [{"content": GOOD_DRAFT,
                                          "usage": {"prompt_tokens": 1000,
                                                    "completion_tokens": 500}}])
        self.post({"model": "api-cheap", "messages": CODE_PROMPT,
                   "modelrouter_project": "demo-proj"})
        env = dict(os.environ)
        result = subprocess.run(
            [sys.executable, "-m", "modelrouter",
             "--config", self.cfg_path, "--data-dir", self.data_dir, "costs"],
            capture_output=True, text=True, cwd=os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("api-cheap", result.stdout)
        self.assertIn("demo-proj", result.stdout)
        self.assertIn("$0.0020", result.stdout)

    def test_budget_stop_returns_429_and_calls_nothing(self):
        import json as _json
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        small_dir = os.path.join(self.tmp.name, "blocked-data")
        os.makedirs(small_dir, exist_ok=True)
        with open(os.path.join(small_dir, "costs.json"), "w") as fh:
            _json.dump({"daily": {day: {"requests": 1, "prompt_tokens": 0,
                                        "completion_tokens": 0,
                                        "cost_usd": 5.0}},
                        "by_model": {}, "by_project": {}}, fh)
        cfg = os.path.join(self.tmp.name, "blocked.yaml")
        with open(cfg, "w") as fh:
            fh.write(_config_text(self.ollama.url, self.openai.url,
                                  daily_usd=1.0))
        proxy, port = self._start_proxy(cfg, small_dir)
        try:
            self.ollama.script("local-fast", [{"content": GOOD_DRAFT}])
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=json.dumps({"model": "cascade",
                                 "messages": CODE_PROMPT}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=15)
                self.fail("expected HTTP 429")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 429)
        finally:
            proxy.shutdown()
            proxy.server_close()
        self.assertEqual(self.ollama.calls_for("local-fast"), [])


if __name__ == "__main__":
    unittest.main()
