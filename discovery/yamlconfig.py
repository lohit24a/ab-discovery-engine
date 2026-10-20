"""Strict reader for the small YAML subset used by config/search_profile.yaml and config/ranking.yaml.

Why this exists: the project plans PyYAML but it is not installed and adding a dependency needs the operator's
approval. This reader covers only what the two config files use, and REFUSES anything else with a line-numbered error,
so a file can never be silently misread.

Deterministic by design: this is the ONLY parser. Parsing never changes because an optional package happens to be
installed (PyYAML 1.1 reads `yes`/`no`/`on`/`off` as booleans, parses dates and octal numbers; this reader does not).
If the operator approves PyYAML, the replacement is a deliberate change: `yaml.safe_load` plus the strict validation in
profile.py, with a test that both readers agree on the shipped files (see docs/design/day12_ranking_audit.md).

Supported: nested mappings (2+ space indentation), block lists ("- item", scalars only), inline lists [a, "b c"],
plain/quoted strings, integers, floats, true/false/null, # comments, blank lines.
Rejected: tabs, anchors/aliases/tags, block scalars (| >), flow mappings ({ }), "- key: value" list items,
duplicate keys, inconsistent indentation, unterminated quotes.
"""

import re
from pathlib import Path


class YamlError(ValueError):
    """The text is outside the supported subset or malformed."""


_KEY = re.compile(r"""^(?P<key>"[^"]+"|'[^']+'|[A-Za-z0-9_][A-Za-z0-9_\-./ ]*?)\s*:(?:\s+(?P<value>.*))?$""")
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def _strip_comment(text, lineno):
    out, quote = [], None
    for i, ch in enumerate(text):
        if quote:
            out.append(ch)
            if ch == quote and text[i - 1] != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    if quote:
        raise YamlError(f"line {lineno}: unterminated quote")
    return "".join(out).rstrip()


def _scalar(text, lineno):
    t = text.strip()
    if t == "":
        return None
    if t[0] in "\"'":
        if len(t) < 2 or t[-1] != t[0]:
            raise YamlError(f"line {lineno}: malformed quoted string {t!r}")
        body = t[1:-1]
        return body.replace('\\"', '"').replace("\\\\", "\\") if t[0] == '"' else body.replace("''", "'")
    if t[0] in "&*!|>{%@`":
        raise YamlError(f"line {lineno}: unsupported YAML construct starting with {t[0]!r}")
    if t[0] == "[":
        return _inline_list(t, lineno)
    if t in ("true", "false"):
        return t == "true"
    if t in ("null", "~"):
        return None
    if _INT.match(t):
        return int(t)
    if _FLOAT.match(t):
        return float(t)
    if ": " in t or t.endswith(":"):
        raise YamlError(f"line {lineno}: an unquoted value may not contain a colon ({t!r}); quote it")
    return t


def _inline_list(text, lineno):
    if not text.endswith("]"):
        raise YamlError(f"line {lineno}: inline list must close on the same line")
    body, items, cur, quote = text[1:-1], [], [], None
    for i, ch in enumerate(body):
        if quote:
            cur.append(ch)
            if ch == quote and body[i - 1] != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if quote:
        raise YamlError(f"line {lineno}: unterminated quote in inline list")
    if "".join(cur).strip() or items:
        items.append("".join(cur))
    out = []
    for raw in items:
        if raw.strip() == "":
            raise YamlError(f"line {lineno}: empty item in inline list")
        if raw.strip()[0] in "[{":
            raise YamlError(f"line {lineno}: nested inline structures are not supported")
        out.append(_scalar(raw, lineno))
    return out


def _tokens(text):
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlError(f"line {n}: tabs are not allowed for indentation")
        stripped = _strip_comment(raw, n)
        if not stripped.strip():
            continue
        if stripped.strip() in ("---", "..."):
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        rows.append((n, indent, stripped.strip()))
    return rows


def _block(rows, i, indent):
    """Parse the block starting at rows[i] whose indentation is `indent`. Returns (value, next_index)."""
    n, ind, content = rows[i]
    if content.startswith("- ") or content == "-":
        out = []
        while i < len(rows) and rows[i][1] == indent and (rows[i][2].startswith("- ") or rows[i][2] == "-"):
            n, _, content = rows[i]
            item = content[1:].strip()
            if _KEY.match(item) and not item.startswith(('"', "'", "[")):
                raise YamlError(f"line {n}: list items that are mappings are not supported")
            out.append(_scalar(item, n))
            i += 1
        if i < len(rows) and rows[i][1] > indent:
            raise YamlError(f"line {rows[i][0]}: unexpected indentation after a list item")
        return out, i
    out = {}
    while i < len(rows) and rows[i][1] == indent:
        n, _, content = rows[i]
        m = _KEY.match(content)
        if not m:
            raise YamlError(f"line {n}: expected 'key: value', got {content!r}")
        key = m.group("key").strip().strip("\"'")
        if key in out:
            raise YamlError(f"line {n}: duplicate key {key!r}")
        value = m.group("value")
        i += 1
        if value is not None and value.strip() != "":
            out[key] = _scalar(value, n)
            if i < len(rows) and rows[i][1] > indent:
                raise YamlError(f"line {rows[i][0]}: unexpected indentation after a value")
        elif i < len(rows) and (rows[i][1] > indent or (rows[i][1] == indent and (rows[i][2].startswith("- ") or rows[i][2] == "-"))):
            child_indent = rows[i][1]
            out[key], i = _block(rows, i, child_indent)
        else:
            out[key] = None
    if i < len(rows) and rows[i][1] > indent:
        raise YamlError(f"line {rows[i][0]}: inconsistent indentation")
    return out, i


def loads(text):
    rows = _tokens(text)
    if not rows:
        return {}
    if rows[0][1] != 0:
        raise YamlError(f"line {rows[0][0]}: the document must start at column 0")
    value, i = _block(rows, 0, 0)
    if i != len(rows):
        raise YamlError(f"line {rows[i][0]}: unexpected content (indentation does not match)")
    return value


def load_yaml(path):
    """Read a config file with the strict subset reader (always; never depends on installed packages)."""
    return loads(Path(path).read_text(encoding="utf-8"))
