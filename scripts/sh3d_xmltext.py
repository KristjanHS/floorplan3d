"""Text-level Home.xml helpers: edit SH3D's XML as text so every untouched byte (quote style, order) is kept.

No cv2/numpy/lxml — cheap to import from any sh3d_* tool."""

import re
from functools import cache

ATTR_RE = re.compile(r"""(\w+)=(['"])(.*?)\2""")
REF_RE = re.compile(r"""\s(?:wallAtStart|wallAtEnd)=(['"])([^'"]*)\1""")


def fmt_num(v: float) -> str:
    """Up to 2 decimals, trailing zeros dropped, never `-0`."""
    out = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if out == "-0" else out


@cache
def element_re(tag: str) -> re.Pattern[str]:
    """One whole `<tag …/>` or `<tag …>…</tag>` element with its line indent and trailing newline."""
    t = re.escape(tag)
    return re.compile(rf"[ \t]*<{t}\b[^>]*?(?:/>|(?<!/)>.*?</{t}>)[ \t]*\n?", re.DOTALL)


def open_attrs(element_xml: str) -> dict[str, str]:
    """Attributes of the element's opening tag (children ignored)."""
    return {k: v for k, _, v in ATTR_RE.findall(element_xml[: element_xml.index(">")])}


def drop_refs(text: str, ids: set[str]) -> str:
    """Remove every `wallAtStart`/`wallAtEnd` attribute that points at one of `ids`."""
    return REF_RE.sub(lambda m: "" if m.group(2) in ids else m.group(0), text)


def set_attrs(tag_text: str, **attrs: str | None) -> str:
    """Set (value) or remove (None) attributes on the element's opening tag; existing ones keep their position and
    quote style, new ones are appended (single-quoted) before `>`/`/>`. Children are left as they are."""
    end = tag_text.index(">")
    head, rest = tag_text[:end], tag_text[end:]
    if head.endswith("/"):
        head, rest = head[:-1], "/" + rest
    for k, v in attrs.items():
        rx = re.compile(rf"""\s{re.escape(k)}=(['"])(.*?)\1""")
        m = rx.search(head)
        if v is None:
            head = rx.sub("", head, count=1)
        elif m:
            q = m.group(1)
            head = head[: m.start()] + f" {k}={q}{v}{q}" + head[m.end() :]
        else:
            head += f" {k}='{v}'"
    return head + rest
