"""Tests for the dashboard + HTML cost report (UI pass).

Covers: budget-bar math, daily chart, tier roles, log sanitization
(no prompt text, no keys, no hashes rendered), the live GET /
dashboard, and the HTML cost report sections. Existing routing/cost
behavior is covered by test_unit.py / test_e2e.py and must stay green.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelrouter import dashboard, server
from modelrouter.config import load_config
from modelrouter.costs import CostTracker
from modelrouter.router import Router

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_CONFIG = os.path.join(ROOT, "config.example.yaml")
FAKE_SECRET = "sk-fake-secret-ui-test-12345"
SECRET_PROMPT = "my very secret prompt text that must never render"


class TestBudgetBar(unittest.TestCase):
    def test_ok_fill_and_markers(self):
        html = dashboard.budget_bar_html(2.5, 5.0, 0.8, 1.0)
        self.assertIn("width: 50.0%", html)
        self.assertIn("left: 80.0%", html)
        self.assertIn("left: 100.0%", html)
        self.assertIn("bar-fill ok", html)

    def test_warn_and_stop_classes(self):
        self.assertIn("bar-fill warn",
                      dashboard.budget_bar_html(4.5, 5.0, 0.8, 1.0))
        self.assertIn("bar-fill stop",
                      dashboard.budget_bar_html(5.0, 5.0, 0.8, 1.0))

    def test_no_cap_is_honest(self):
        html = dashboard.budget_bar_html(1.0, None, 0.8, 1.0)
        self.assertIn("No daily budget cap", html)
        self.assertNotIn("bar-fill", html)

    def test_fill_capped_at_100(self):
        self.assertIn("width: 100.0%",
                      dashboard.budget_bar_html(99.0, 5.0, 0.8, 1.0))


class TestDailyBars(unittest.TestCase):
    def test_proportional_heights(self):
        state = {"daily": {
            "2026-11-01": {"requests": 1, "prompt_tokens": 10,
                           "completion_tokens": 5, "cost_usd": 1.0},
            "2026-11-02": {"requests": 1, "prompt_tokens": 10,
                           "completion_tokens": 5, "cost_usd": 0.5},
        }}
        html = dashboard.daily_bars_html(state)
        self.assertIn("height: 100%", html)
        self.assertIn("height: 50%", html)

    def test_empty_state(self):
        self.assertIn("No usage recorded yet",
                      dashboard.daily_bars_html({"daily": {}}))


class TestTierRoles(unittest.TestCase):
    def test_roles_from_example_config(self):
        config = load_config(EXAMPLE_CONFIG)
        roles = dashboard.tier_roles(config, "qwen3-local")
        self.assertIn("single:model", roles)
        self.assertIn("cascade:draft", roles)
        self.assertIn("critique:primary", roles)
        roles = dashboard.tier_roles(config, "gpt-strong")
        self.assertIn("critique:critic", roles)

    def test_unknown_model_is_dash(self):
        config = load_config(EXAMPLE_CONFIG)
        self.assertEqual("—", dashboard.tier_roles(config, "nope"))


class TestSanitization(unittest.TestCase):
    def _log_with_junk(self, tmp):
        path = os.path.join(tmp, "routing.jsonl")
        entry = {
            "ts": "2026-11-02T12:00:00Z", "request_id": "abcdef1234567890",
            "prompt_hash": "deadbeef" * 8, "tier": "cascade",
            "effort": "medium", "project": None,
            "models_tried": ["qwen3-local", "gpt-cheap"],
            "escalation_reason": "refusal+missing_code_block",
            "prompt_tokens": 100, "completion_tokens": 50,
            "cost_usd": 0.0012, "duration_ms": 123.4, "outcome": "ok",
            # Junk that must never reach the page:
            "prompt": SECRET_PROMPT, "api_key": FAKE_SECRET,
        }
        with open(path, "w") as fh:
            fh.write(json.dumps(entry) + "\n")
        return path

    def test_recent_decisions_drops_unsafe_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = dashboard.recent_decisions(self._log_with_junk(tmp))
            self.assertEqual(1, len(entries))
            self.assertNotIn("prompt_hash", entries[0])
            self.assertNotIn("prompt", entries[0])
            self.assertNotIn("api_key", entries[0])
            self.assertEqual(["qwen3-local", "gpt-cheap"],
                             entries[0]["models_tried"])

    def test_reason_prettified(self):
        self.assertEqual("refusal → missing code block",
                         dashboard._prettify_reason("refusal+missing_code_block"))
        self.assertEqual("—", dashboard._prettify_reason(None))


class TestStatusPage(unittest.TestCase):
    def _render(self):
        os.environ["OPENAI_API_KEY"] = FAKE_SECRET
        try:
            config = load_config(EXAMPLE_CONFIG)
            with tempfile.TemporaryDirectory() as tmp:
                tracker = CostTracker(os.path.join(tmp, "costs.json"))
                tracker.record("qwen3-local",
                               {"prompt_tokens": 100, "completion_tokens": 50},
                               0.0, None)
                return dashboard.render_status_page(
                    config, tracker, 3725.0, os.path.join(tmp, "routing.jsonl"),
                    "http://127.0.0.1:4000")
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_contains_sections_and_models(self):
        html = self._render()
        for needle in ("Recent routing decisions", "How routing works",
                       "Today's budget", "Point your agent at it",
                       "qwen3-local", "gpt-cheap", "gpt-strong",
                       "Single", "Cascade", "Critique", "1h 2m"):
            self.assertIn(needle, html)

    def test_never_leaks_key(self):
        self.assertNotIn(FAKE_SECRET, self._render())
        self.assertIn("key set", self._render())

    def test_design_rules(self):
        html = self._render()
        self.assertNotIn("linear-gradient", html)
        self.assertIn("border-radius: 0", html)
        self.assertNotIn("border-radius: 8px", html)


class TestHtmlCostReport(unittest.TestCase):
    def test_sections_and_rows(self):
        tracker_state = {
            "daily": {"2026-11-02": {"requests": 3, "prompt_tokens": 300,
                                     "completion_tokens": 150,
                                     "cost_usd": 0.0025}},
            "by_model": {"qwen3-local": {"requests": 3, "prompt_tokens": 300,
                                          "completion_tokens": 150,
                                          "cost_usd": 0.0025}},
            "by_project": {"demo": {"requests": 3, "prompt_tokens": 300,
                                     "completion_tokens": 150,
                                     "cost_usd": 0.0025}},
        }
        html = dashboard.render_cost_report(tracker_state, cap=5.0)
        for needle in ("Spend by day", "Spend by model", "Spend by project",
                       "Today's budget", "qwen3-local", "demo", "chart"):
            self.assertIn(needle, html)


class TestLiveDashboard(unittest.TestCase):
    """GET / on a real proxy: 200, HTML, no secrets, decisions shown."""

    def test_live_dashboard(self):
        os.environ["OPENAI_API_KEY"] = FAKE_SECRET
        try:
            config = load_config(EXAMPLE_CONFIG)
            with tempfile.TemporaryDirectory() as tmp:
                data_dir = os.path.join(tmp, "data")
                os.makedirs(data_dir)
                log_path = os.path.join(data_dir, "routing.jsonl")
                with open(log_path, "w") as fh:
                    fh.write(json.dumps({
                        "ts": "2026-11-02T12:00:00Z",
                        "request_id": "abcdef1234567890",
                        "prompt_hash": "deadbeef" * 8, "tier": "cascade",
                        "effort": "medium", "project": None,
                        "models_tried": ["qwen3-local", "gpt-cheap"],
                        "escalation_reason": "refusal",
                        "prompt_tokens": 100, "completion_tokens": 50,
                        "cost_usd": 0.0012, "duration_ms": 10.0,
                        "outcome": "ok",
                        "prompt": SECRET_PROMPT,
                    }) + "\n")
                tracker = CostTracker(os.path.join(data_dir, "costs.json"))
                router = Router(config, tracker, log_path)
                httpd = server.serve(config, tracker, router, "127.0.0.1", 0)
                thread = threading.Thread(target=httpd.serve_forever,
                                          daemon=True)
                thread.start()
                try:
                    port = httpd.server_address[1]
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/", timeout=15) as resp:
                        self.assertEqual(200, resp.status)
                        self.assertIn("text/html",
                                      resp.headers.get("Content-Type", ""))
                        html = resp.read().decode()
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                self.assertIn("Recent routing decisions", html)
                self.assertIn("gpt-cheap", html)
                self.assertNotIn(FAKE_SECRET, html)
                self.assertNotIn(SECRET_PROMPT, html)
                self.assertNotIn("deadbeef", html)
        finally:
            os.environ.pop("OPENAI_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
