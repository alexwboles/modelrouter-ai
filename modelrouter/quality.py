"""Output-quality gates for the Cascade tier.

The gates are deliberately simple and honest — they catch the failures
a cheap draft actually has, without pretending to judge semantic
correctness:

- ``empty_output``: the model returned nothing at all.
- ``too_short``: fewer than ``min_length`` characters (configurable).
- ``refusal``: the text matches a refusal pattern (configurable list).
- ``missing_code_block``: the user asked for code but the answer has no
  fenced code block.

A draft passes only when every gate passes. The exact failing gate
names are logged as the escalation reason, so an operator can see why
a stronger (and pricier) model was called.
"""

import re

DEFAULT_REFUSAL_PATTERNS = [
    "i can't",
    "i cannot",
    "i'm unable",
    "i am unable",
    "i'm sorry, but i can't",
    "sorry, i can't",
    "i don't have the ability",
    "i am not able to",
    "as an ai language model",
    "as an ai, i",
]

CODE_REQUEST_WORDS = (
    "code",
    "function",
    "implement",
    "script",
    "program",
    "class",
    "snippet",
    "write a",
    "fix the",
    "debug",
)


def code_requested(messages) -> bool:
    """True when the latest user message looks like a request for code."""
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content") or ""
            if isinstance(content, list):  # multi-part content blocks
                content = " ".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
            lowered = content.lower()
            return any(word in lowered for word in CODE_REQUEST_WORDS)
    return False


def check_output(content: str, messages, min_length: int = 40,
                  refusal_patterns=None) -> list:
    """Return the list of failing gate names (empty = draft accepted)."""
    failures = []
    text = content or ""
    if not text.strip():
        failures.append("empty_output")
        return failures  # nothing else is meaningful on empty output
    if len(text.strip()) < min_length:
        failures.append("too_short")
    lowered = text.lower()
    patterns = refusal_patterns if refusal_patterns is not None else DEFAULT_REFUSAL_PATTERNS
    if any(pat.lower() in lowered for pat in patterns):
        failures.append("refusal")
    if code_requested(messages) and "```" not in text:
        failures.append("missing_code_block")
    return failures
