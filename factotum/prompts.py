"""What every prompt builder does with text it did not write (polish 2026-11-11).

    fill(template, values) -> str      every {{name}} filled in ONE pass: a value is never scanned again, so a
                                       task name holding "{{rows}}" stays those characters (ripple.build_prompt
                                       already worked this way)
    one_line(v) -> str                 a value written on one line of a record: every run of whitespace becomes
                                       one space, which removes every character str.splitlines() splits on
                                       (\\n \\r \\x0b \\x0c \\x1c-\\x1e U+0085 U+2028 U+2029), so it cannot start a line
    esc_line(v) -> str                 one_line, then HTML-escaped (the planner's, the summary's and mail
                                       triage's record lines keep the escape their tests pin)
    unescape_reply(data)               a reply's strings with those entities turned back, before anything of
                                       it is stored: a name the model copied from an escaped line is "Q&A",
                                       never "Q&amp;A"
    json_breaks(text) -> str           JSON text with U+0085, U+2028 and U+2029 written as escapes (json.dumps
                                       with ensure_ascii=False leaves them raw, and a reader may break a line there)
"""
from __future__ import annotations

import html
import re

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_BREAKS = str.maketrans({" ": "\\u2028", " ": "\\u2029", "\x85": "\\u0085"})


def fill(template: str, values: dict) -> str:
    """Each {{name}} in `values` replaced by its value, in one pass; an unknown {{name}} is left as it is."""
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def one_line(v) -> str:
    return " ".join(str(v if v is not None else "").split())


def esc_line(v) -> str:
    return html.escape(one_line(v), quote=False)


def unescape_reply(data):
    if isinstance(data, str):
        return html.unescape(data)
    if isinstance(data, list):
        return [unescape_reply(x) for x in data]
    if isinstance(data, dict):
        return {k: unescape_reply(v) for k, v in data.items()}
    return data


def json_breaks(text: str) -> str:
    return text.translate(_BREAKS)
