"""Minimal YAML subset parser (standard library only).

modelrouter-ai deliberately has no third-party dependencies, so it ships
its own small YAML reader covering exactly the shapes a router config
needs. Supported:

- ``key: value`` mappings, nested by indentation (2 spaces recommended)
- lists with ``- item``; list items may be ``- key: value`` dicts whose
  remaining keys sit at the item's content indent
- a list at the same indent as its parent key (common YAML style)
- scalars: plain strings, single/double-quoted strings, ints, floats,
  booleans (true/false), null (``~`` or empty)
- inline flow collections: ``{a: b, c: d}`` and ``[a, b]``
- ``#`` comments (outside quotes) and blank lines

NOT supported (use a full YAML library if you need them): multi-line
block scalars (``|`` / ``>``), anchors/aliases, tags, multi-document
files, and keys containing a colon. If the config uses an unsupported
shape, loading fails loudly with the line number instead of silently
misreading it.
"""

import re


class YamlMiniError(ValueError):
    """Raised when the config file uses YAML outside the supported subset."""


def _strip_comment(line: str) -> str:
    """Remove a trailing ``# comment`` that is not inside quotes."""
    in_single = in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            if in_single and i + 1 < len(line) and line[i + 1] == "'":
                i += 2
                continue
            in_single = not in_single
        elif ch == '"' and not in_single:
            if i > 0 and line[i - 1] == "\\":
                pass
            else:
                in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            # A comment starts at '#' preceded by whitespace or line start.
            if i == 0 or line[i - 1] in " \t":
                return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _split_flow(text: str):
    """Split a flow collection's inner text on top-level commas."""
    parts, depth, cur, in_single, in_double = [], 0, "", False, False
    for ch in text:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(cur.strip())
                cur = ""
                continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _split_key_value(text: str):
    """Split ``key: value`` on the first colon followed by space or EOL."""
    in_single = in_double = False
    for i, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if i + 1 == len(text) or text[i + 1] in " \t":
                return text[:i].strip(), text[i + 1 :].strip()
    return None, None


def parse_scalar(text: str):
    """Parse one scalar or flow value."""
    text = text.strip()
    if text == "" or text == "~" or text.lower() == "null":
        return None
    if text.startswith("{") and text.endswith("}"):
        out = {}
        for part in _split_flow(text[1:-1]):
            key, value = _split_key_value(part)
            if key is None:
                raise YamlMiniError(f"bad flow mapping entry: {part!r}")
            out[_unquote(key)] = parse_scalar(value)
        return out
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(p) for p in _split_flow(inner)]
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return bytes(text[1:-1], "utf-8").decode("unicode_escape")
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?", text):
        return float(text)
    return text


def _unquote(text: str):
    value = parse_scalar(text)
    return value if isinstance(value, str) else text


class _Line:
    __slots__ = ("indent", "text", "number", "is_list")

    def __init__(self, indent, text, number):
        self.indent = indent
        self.text = text
        self.number = number
        self.is_list = text == "-" or text.startswith("- ")


def _prepare(text: str):
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlMiniError(f"line {number}: tabs are not allowed for indentation")
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append(_Line(indent, stripped.strip(), number))
    return lines


def _parse_block(lines, i, indent):
    """Parse a mapping or list block starting at lines[i] with `indent`."""
    if i >= len(lines):
        return None, i
    if lines[i].is_list and lines[i].indent == indent:
        out = []
        while i < len(lines) and lines[i].indent == indent and lines[i].is_list:
            content = lines[i].text[1:].strip()
            start_no = lines[i].number
            i += 1
            if not content:
                # Nested block on the following, deeper-indented lines.
                if i < len(lines) and lines[i].indent > indent:
                    sub, i = _parse_block(lines, i, lines[i].indent)
                    out.append(sub)
                else:
                    out.append(None)
                continue
            key, value = _split_key_value(content)
            if key is not None:
                item = {_unquote(key): parse_scalar(value) if value else None}
                if value == "":
                    # Value is a nested block on following lines.
                    if i < len(lines) and lines[i].indent > indent:
                        sub, i = _parse_block(lines, i, lines[i].indent)
                        item[_unquote(key)] = sub
                else:
                    # Remaining dict keys sit deeper than the dash.
                    if i < len(lines) and lines[i].indent > indent:
                        sub, i = _parse_block(lines, i, lines[i].indent)
                        if not isinstance(sub, dict):
                            raise YamlMiniError(
                                f"line {start_no}: expected mapping keys after "
                                f"list item, got a list/scalar"
                            )
                        item.update(sub)
                out.append(item)
            else:
                out.append(parse_scalar(content))
        return out, i
    # Mapping block.
    out = {}
    while i < len(lines) and lines[i].indent == indent and not lines[i].is_list:
        key, value = _split_key_value(lines[i].text)
        if key is None:
            raise YamlMiniError(
                f"line {lines[i].number}: expected 'key: value', got "
                f"{lines[i].text!r}"
            )
        key = _unquote(key)
        i += 1
        if value == "":
            if i < len(lines) and (
                lines[i].indent > indent
                or (lines[i].indent == indent and lines[i].is_list)
            ):
                sub, i = _parse_block(lines, i, lines[i].indent)
                out[key] = sub
            else:
                out[key] = None
        else:
            out[key] = parse_scalar(value)
    return out, i


def parse(text: str):
    """Parse YAML-subset text into dicts/lists/scalars."""
    lines = _prepare(text)
    if not lines:
        return {}
    result, i = _parse_block(lines, 0, lines[0].indent)
    if i != len(lines):
        raise YamlMiniError(f"line {lines[i].number}: unexpected content")
    return result


def load_file(path: str):
    """Parse a YAML-subset file."""
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return parse(fh.read())
        except YamlMiniError as exc:
            raise YamlMiniError(f"{path}: {exc}") from exc
