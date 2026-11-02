"""Unit tests: parser, config, gates, cost math, budgets, redaction,
tier selection. No network, no servers."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modelrouter import quality, yamlmini
from modelrouter.config import ConfigError, load_config
from modelrouter.costs import CostTracker, cost_usd
from modelrouter.redact import prompt_hash, redact_obj, redact_text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestYamlMini(unittest.TestCase):
    def test_example_config_parses(self):
        data = yamlmini.load_file(os.path.join(REPO, "config.example.yaml"))
        self.assertEqual(len(data["models"]), 3)
        self.assertEqual(data["models"][0]["id"], "qwen3-local")
        self.assertEqual(data["models"][0]["input_price"], 0.0)
        self.assertEqual(data["tiers"]["cascade"]["min_length"], 40)
        self.assertEqual(data["effort"]["low"]["cascade"]["strong"], "qwen3-local")
        self.assertEqual(data["effort"]["medium"], {})
        self.assertEqual(data["policies"]["budget"]["daily_usd"], 5.0)
        self.assertEqual(data["policies"]["budget"]["warn_fraction"], 0.8)

    def test_flow_collections_and_quotes(self):
        data = yamlmini.parse(
            "a: {x: 1, y: [2, 3]}\n"
            "b: 'it''s quoted # not a comment'\n"
            'c: "with # hash"\n'
            "d: true\n"
            "e: 2.5\n"
            "f: ~\n"
            "# full line comment\n"
            "g: plain # trailing comment\n"
        )
        self.assertEqual(data["a"], {"x": 1, "y": [2, 3]})
        self.assertEqual(data["b"], "it's quoted # not a comment")
        self.assertEqual(data["c"], "with # hash")
        self.assertIs(data["d"], True)
        self.assertEqual(data["e"], 2.5)
        self.assertIsNone(data["f"])
        self.assertEqual(data["g"], "plain")

    def test_list_of_dicts_same_indent_style(self):
        data = yamlmini.parse(
            "items:\n- id: one\n  val: 1\n- id: two\n  val: 2\n"
        )
        self.assertEqual(data["items"],
                         [{"id": "one", "val": 1}, {"id": "two", "val": 2}])

    def test_bad_shape_raises_with_line(self):
        with self.assertRaises(yamlmini.YamlMiniError):
            yamlmini.parse("not a mapping line at all\n  indented: junk\n")


class TestConfig(unittest.TestCase):
    def test_example_loads_and_resolves(self):
        config = load_config(os.path.join(REPO, "config.example.yaml"))
        self.assertEqual(config.resolve("cascade", "low")["strong"], "qwen3-local")
        self.assertEqual(config.resolve("cascade", "medium")["strong"], "gpt-cheap")
        self.assertEqual(config.resolve("single", "high")["model"], "gpt-strong")
        self.assertEqual(config.policies.default_tier, "cascade")

    def test_unknown_model_in_tier_rejected(self):
        path = os.path.join(REPO, "bad-config.yaml")
        with open(path, "w") as fh:
            fh.write(
                "models:\n- id: only\n  endpoint: http://127.0.0.1:9/v1\n"
                "tiers:\n  single: {model: only}\n"
                "  cascade: {draft: only, strong: ghost}\n"
                "  critique: {primary: only, critic: only}\n"
            )
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.remove(path)

    def test_unknown_tier_and_effort_rejected(self):
        config = load_config(os.path.join(REPO, "config.example.yaml"))
        with self.assertRaises(ConfigError):
            config.resolve("nope", "medium")
        with self.assertRaises(ConfigError):
            config.resolve("cascade", "ultra")


class TestQualityGates(unittest.TestCase):
    MESSAGES = [{"role": "user", "content": "write a function that adds two numbers"}]

    def test_empty(self):
        self.assertEqual(
            quality.check_output("", self.MESSAGES), ["empty_output"])
        self.assertEqual(
            quality.check_output("   ", self.MESSAGES), ["empty_output"])

    def test_too_short(self):
        self.assertIn("too_short",
                      quality.check_output("ok", self.MESSAGES, min_length=40))

    def test_refusal(self):
        self.assertIn("refusal", quality.check_output(
            "I'm sorry, but I can't help with that request at all, really.",
            [{"role": "user", "content": "hello"}]))

    def test_missing_code_block_only_when_code_requested(self):
        long_plain = "Here is a detailed explanation of the algorithm. " * 3
        self.assertIn("missing_code_block",
                      quality.check_output(long_plain, self.MESSAGES))
        self.assertNotIn("missing_code_block", quality.check_output(
            long_plain, [{"role": "user", "content": "explain photosynthesis"}]))

    def test_good_code_answer_passes(self):
        good = "Here is the function:\n```python\ndef add(a, b):\n    return a + b\n```\n"
        self.assertEqual(quality.check_output(good, self.MESSAGES), [])


class TestCostMath(unittest.TestCase):
    class _Model:
        input_price = 1.00
        output_price = 2.00

    def test_cost_math(self):
        amount = cost_usd(self._Model(), {"prompt_tokens": 1_000_000,
                                          "completion_tokens": 500_000})
        self.assertAlmostEqual(amount, 2.00)

    def test_missing_usage_costs_zero(self):
        self.assertEqual(cost_usd(self._Model(), {}), 0.0)


class TestBudget(unittest.TestCase):
    def _tracker_with_spend(self, tmp, amount):
        import json
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        state = {"daily": {day: {"requests": 1, "prompt_tokens": 0,
                                 "completion_tokens": 0, "cost_usd": amount}},
                 "by_model": {}, "by_project": {}}
        path = os.path.join(tmp, "costs.json")
        with open(path, "w") as fh:
            json.dump(state, fh)
        return CostTracker(path)

    def test_warn_and_stop_thresholds(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tracker = self._tracker_with_spend(tmp, 4.00)
            self.assertEqual(tracker.budget_status(5.0, 0.8, 1.0), "warn")
            tracker = self._tracker_with_spend(tmp, 5.00)
            self.assertEqual(tracker.budget_status(5.0, 0.8, 1.0), "stop")
            tracker = self._tracker_with_spend(tmp, 3.99)
            self.assertEqual(tracker.budget_status(5.0, 0.8, 1.0), "ok")
            tracker = self._tracker_with_spend(tmp, 99.0)
            self.assertEqual(tracker.budget_status(None, 0.8, 1.0), "ok")

    def test_markdown_report_totals(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tracker = self._tracker_with_spend(tmp, 4.00)
            tracker.record("api-cheap", {"prompt_tokens": 1000,
                                         "completion_tokens": 500}, 0.002, "demo")
            report = tracker.markdown_report()
            self.assertIn("Spend by day", report)
            self.assertIn("api-cheap", report)
            self.assertIn("demo", report)


class TestRedaction(unittest.TestCase):
    def test_bearer_and_kv_redacted(self):
        text = redact_text("Authorization: Bearer sk-testsecret123 and api_key=abc")
        self.assertNotIn("sk-testsecret123", text)
        self.assertNotIn("abc", text)
        self.assertIn("***REDACTED***", text)

    def test_redact_obj(self):
        cleaned = redact_obj({"api_key": "hunter2", "model": "x",
                              "nested": {"token": "zzz"}})
        self.assertEqual(cleaned["api_key"], "***REDACTED***")
        self.assertEqual(cleaned["model"], "x")
        self.assertEqual(cleaned["nested"]["token"], "***REDACTED***")

    def test_prompt_hash_stable_and_content_free(self):
        messages = [{"role": "user", "content": "top secret prompt"}]
        self.assertEqual(prompt_hash(messages), prompt_hash(messages))
        self.assertNotEqual(prompt_hash(messages),
                           prompt_hash([{"role": "user", "content": "other"}]))
        self.assertNotIn("top secret", prompt_hash(messages))


if __name__ == "__main__":
    unittest.main()
