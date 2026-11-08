import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog.models import Offer

pytestmark = pytest.mark.usefixtures("frozen_now")

CSS = Path(__file__).resolve().parents[2] / "web" / "static" / "web" / "app.css"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []  # (tag, attrs dict)
        self.label_for = set()
        self.in_label = 0
        self.wrapped = set()  # indexes of controls inside a label
        self.h1 = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag == "h1":
            self.h1 += 1
        if tag in ("input", "select", "textarea"):
            if self.in_label:
                self.wrapped.add(len(self.tags))
            self.tags.append((tag, a))
        elif tag == "html":
            self.tags.append((tag, a))

    def handle_endtag(self, tag):
        if tag == "label":
            self.in_label -= 1

    def unlabelled(self):
        bad = []
        for i, (tag, a) in enumerate(self.tags):
            if tag == "html" or a.get("type") in ("hidden", "submit"):
                continue
            if a.get("aria-label") or i in self.wrapped or (a.get("id") and a["id"] in self.label_for):
                continue
            bad.append((tag, a))
        return bad


def parse(client, url):
    p = Page()
    p.feed(client.get(url).content.decode())
    return p


@pytest.fixture
def urls(milk_rows):
    return ["/", "/c/dairy/milk/", "/search?q=mystery", "/settings", "/disclosure"]


def test_every_input_has_label(client, urls):
    for url in urls:
        page = parse(client, url)
        assert page.tags, url
        assert page.unlabelled() == [], url
    assert sum(1 for t, _ in parse(client, "/c/dairy/milk/").tags if t in ("input", "select")) > 5


def test_single_h1(client, urls):
    for url in urls:
        assert parse(client, url).h1 == 1, url


def test_html_lang(client, urls):
    for url in urls:
        html = [a for t, a in parse(client, url).tags if t == "html"][0]
        assert html.get("lang") == "en", url


def test_price_announcement(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    labels = re.findall(r'aria-label="(price [^"]*)"', html)
    assert len(labels) >= 4
    assert any(re.fullmatch(r"price \$1\.00, \$[\d.]+ per [\w ]+", s) for s in labels)
    assert any(re.fullmatch(r"price \$3\.00", s) for s in labels)  # no unit price


def test_search_price_announcement(client, milk_rows):
    html = client.get("/search?q=mystery").content.decode()
    assert re.search(r'aria-label="price \$\d+\.\d\d"', html)


def test_filters_in_drawer_and_viewport(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert re.search(r'<details[^>]*class="filters-drawer"[^>]*>\s*<summary>', html)
    assert 'name="viewport"' in html


def _vars(block: str) -> dict:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def _themes():
    css = CSS.read_text()
    light = _vars(re.search(r":root\s*\{(.*?)\}", css, re.S).group(1))
    dark = {**light, **_vars(re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}", css, re.S).group(1))}
    return {"light": light, "dark": dark}


def _lum(hex_):
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(hex_[i : i + 2]) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PAIRS = [("fg", "bg"), ("fg", "surface"), ("muted", "bg"), ("muted", "surface"),
         ("accent", "bg"), ("accent", "surface"), ("accent-fg", "accent")]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_contrast(theme):
    v = _themes()[theme]
    for fg, bg in PAIRS:
        assert contrast(v[fg], v[bg]) >= 4.5, (theme, fg, bg, contrast(v[fg], v[bg]))


def test_no_hardcoded_colours_outside_variable_blocks():
    css = CSS.read_text()
    rest = re.sub(r"(:root\s*\{.*?\}|@media \(prefers-color-scheme: dark\)\s*\{.*?\}\s*\})", "", css, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", rest)


def test_demo_data_refuses_without_debug(db, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("demo_data")
    assert not Offer.objects.exists()


def test_demo_data_loads_milk_offers(db, settings, capsys):
    settings.DEBUG = True
    call_command("demo_data")
    assert Offer.objects.filter(retailer="walmart").exists()
    assert "http://127.0.0.1:8000/c/dairy/milk/" in capsys.readouterr().out
