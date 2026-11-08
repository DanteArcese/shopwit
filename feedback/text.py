"""Store-name normalization and Markdown neutralization for user text sent to GitHub."""

import re
import unicodedata

_APOSTROPHES = "'’‘ʼ`´"


def store_key(name: str) -> str:
    """Normalized store name: "Trader Joe's" == "trader joes" == "The Trader Joes"."""
    text = unicodedata.normalize("NFKC", name or "").casefold()
    text = "".join(c for c in text if c not in _APOSTROPHES)
    text = "".join(" " if unicodedata.category(c)[0] in "PSZC" else c for c in text)
    return " ".join(w for w in text.split() if w != "the")


def display_name(name: str) -> str:
    """Store name for an issue title: single-spaced, no control characters."""
    text = "".join(c for c in name or "" if unicodedata.category(c)[0] != "C")
    return " ".join(text.split())[:120]


# Order matters: emails before mentions, so "a@b.com" is never read as "@b".
_TOKEN = re.compile(
    r"(?P<email>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)"
    r"|(?P<url>(?:https?://|www\.|mailto:|xmpp:)[^\s<>`]+)"
    r"|(?P<ref>(?<![\w/])[\w.-]+/[\w.-]+#\d+|(?<![\w&])#\d+\b|\bGH-\d+\b)"
    r"|(?P<mention>(?<![A-Za-z0-9])@[A-Za-z0-9][A-Za-z0-9-]*(?:/[\w.-]+)?)",
    re.IGNORECASE,
)
_ESCAPES = str.maketrans(
    {"\\": "\\\\", "`": "\\`", "[": "\\[", "]": "\\]", "<": "&lt;", ">": "&gt;"}
)


def _escape(text: str) -> str:
    return text.translate(_ESCAPES)


def neutralize(text: str) -> str:
    """Make user text inert in GitHub Markdown.

    Mentions, issue references and bare links become code spans (no pings, no
    backlinks, nothing clickable); Markdown link/image brackets, backticks and
    HTML are escaped; email addresses are removed.
    """
    out = []
    pos = 0
    for m in _TOKEN.finditer(text):
        out.append(_escape(text[pos : m.start()]))
        token, tail = m.group(), ""
        if m.lastgroup == "url":  # trailing punctuation belongs to the sentence
            token = token.rstrip(").,;:!?'\"")
            tail = m.group()[len(token) :]
        out.append(
            _escape("[email removed]") if m.lastgroup == "email" else f"`{token}`"
        )
        out.append(_escape(tail))
        pos = m.end()
    out.append(_escape(text[pos:]))
    return "".join(out)


def quote(text: str) -> str:
    """Markdown blockquote of (already neutralized) text."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(f"> {line}" for line in lines)
