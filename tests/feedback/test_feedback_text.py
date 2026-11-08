import pytest

from feedback.text import neutralize, quote, store_key, title_safe

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "name",
    [
        "Trader Joe's",
        "trader joes",
        "The Trader Joes",
        "  TRADER   JOE’S!  ",
        "the trader-joe's",
    ],
)
def test_store_key_normalization(name):
    # Hyphens split words, so "trader-joe's" is "trader joes" too.
    assert store_key(name) == "trader joes"


def test_store_key_distinct_and_empty():
    assert store_key("Aldi") != store_key("Lidl")
    assert store_key("H-E-B") == "h e b"
    assert store_key("The") == ""
    assert store_key("!!!") == ""
    assert store_key("Theodore's Market") == "theodores market"


def test_mentions_wrapped_in_code():
    out = neutralize("ping @octocat and @org/team please")
    assert "`@octocat`" in out
    assert "`@org/team`" in out
    assert " @octocat" not in out.replace("`@octocat`", "")


def test_links_wrapped_not_clickable():
    out = neutralize("see https://spam.example/buy?x=1 or www.spam.example now")
    assert "`https://spam.example/buy?x=1`" in out
    assert "`www.spam.example`" in out


def test_markdown_links_images_and_html_escaped():
    out = neutralize(
        "[click](https://evil.example) ![x](https://evil.example/p.png) <img src=x>"
    )
    assert "\\[click\\]" in out
    assert "!\\[x\\]" in out
    assert "<img" not in out and "&lt;img" in out
    assert "`https://evil.example`" in out


def test_backticks_cannot_break_out():
    out = neutralize("`@octocat` `` x")
    assert "\\`" in out
    assert "`@octocat`" in out


def test_emails_redacted_and_cross_refs_neutralized():
    out = neutralize("mail me at a.b@example.com about other/repo#12 and #3")
    assert "example.com" not in out
    assert "[email removed]" not in out  # brackets are escaped
    assert "\\[email removed\\]" in out
    assert "`other/repo#12`" in out
    assert "`#3`" in out


def test_backslash_cannot_unescape():
    out = neutralize("\\[x](y)")
    assert out.startswith("\\\\\\[x\\]")


def test_quote_prefixes_every_line():
    assert quote("one\r\ntwo\n\nthree") == "> one\n> two\n> \n> three"


def test_store_key_truncated_to_field_length():
    from feedback.models import Feedback, StoreRequest

    limit = Feedback._meta.get_field("store_key").max_length
    assert StoreRequest._meta.get_field("store_key").max_length == limit
    assert len(store_key("ß" * 120)) == limit
    assert not store_key("a " * 100 + "b").endswith(" ")


def test_ampersand_escaped():
    assert neutralize("salt & pepper &lt;") == "salt &amp; pepper &amp;lt;"


def test_title_safe_removes_email_and_wraps_tokens():
    out = title_safe("Bob's bob@example.com @octocat https://x.example #5")
    assert "example.com" not in out
    assert "[email removed]" in out
    assert "`@octocat`" in out and "`https://x.example`" in out and "`#5`" in out
    assert title_safe("a`b") == "ab"
