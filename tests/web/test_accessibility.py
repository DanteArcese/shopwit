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
        self.labels = []  # {"for": id or None, "text": str, "controls": [tag indexes]}
        self.open_label = None
        self.h1 = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.open_label = {"for": a.get("for"), "text": "", "controls": []}
            self.labels.append(self.open_label)
        if tag == "h1":
            self.h1 += 1
        if tag in ("input", "select", "textarea"):
            if self.open_label is not None:
                self.open_label["controls"].append(len(self.tags))
            self.tags.append((tag, a))
        elif tag == "html":
            self.tags.append((tag, a))

    def handle_data(self, data):
        if self.open_label is not None:
            self.open_label["text"] += data

    def handle_endtag(self, tag):
        if tag == "label":
            self.open_label = None

    def unlabelled(self):
        """Controls with no accessible name: aria-label or a label, each with non-empty text."""
        named = set()
        for label in self.labels:
            if not label["text"].strip():
                continue
            named.update(label["controls"])
            if label["for"]:
                named.update(i for i, (_, a) in enumerate(self.tags) if a.get("id") == label["for"])
        bad = []
        for i, (tag, a) in enumerate(self.tags):
            if tag == "html" or a.get("type") in ("hidden", "submit"):
                continue
            if (a.get("aria-label") or "").strip() or i in named:
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
    labels = re.findall(r'<span class="visually-hidden">(price [^<]*)</span>', html)
    assert len(labels) >= 4
    assert any(re.fullmatch(r"price \$1\.00, \$[\d.]+ per [\w ]+", s) for s in labels)
    assert any(re.fullmatch(r"price \$3\.00", s) for s in labels)  # no unit price


def test_search_price_announcement(client, milk_rows):
    html = client.get("/search?q=mystery").content.decode()
    assert re.search(r'<span class="visually-hidden">price \$\d+\.\d\d</span>', html)


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


def _token_uses(prop: str) -> set:
    """Colour tokens the stylesheet uses for a property (e.g. `color: var(--fg)`)."""
    return set(re.findall(rf"(?<![\w-]){prop}:\s*var\(--([\w-]+)\)", CSS.read_text()))


def _pairs():
    """Text/background pairs derived from the CSS: every text token over every
    background token it can sit on. A `<bg>-fg` token pairs only with `<bg>`, and
    a background that has such a token (accent) takes only that text."""
    texts = _token_uses("color")
    backgrounds = _token_uses("background")
    pairs = set()
    for fg in texts:
        for bg in backgrounds:
            if fg == bg or (fg.endswith("-fg") and fg[: -len("-fg")] != bg):
                continue
            if f"{bg}-fg" in texts and fg != f"{bg}-fg":
                continue
            pairs.add((fg, bg))
    return sorted(pairs)


def test_contrast_pairs_derived_from_css():
    pairs = _pairs()
    for expected in [("fg", "bg"), ("fg", "surface"), ("muted", "bg"), ("muted", "surface"),
                     ("accent", "bg"), ("accent", "surface"), ("accent-fg", "accent")]:
        assert expected in pairs, expected


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_contrast(theme):
    v = _themes()[theme]
    for fg, bg in _pairs():
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
    assert "/c/dairy/milk/" in capsys.readouterr().out


# ---- fix round 1 ----
JS = CSS.parent / "app.js"


def test_drawer_reopens_when_widened():
    js = JS.read_text()
    assert 'matchMedia("(min-width: 48rem)")' in js and "addEventListener" in js and "open = true" in js
    css = CSS.read_text()
    assert re.search(r"@media \(min-width: 48rem\)\s*\{[^@]*filters-drawer:not\(\[open\]\)::details-content", css, re.S)


def test_times_localizable(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert re.search(r'<time data-localize datetime="2026-11-07T12:00:00\+00:00"', html)
    js = JS.read_text()
    assert "time[data-localize]" in js and "htmx:afterSwap" in js and "Intl.DateTimeFormat" in js


def test_stores_hint_all_when_none_selected(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "All stores" in html


def test_stores_hint_hidden_when_selected(client, milk_rows):
    html = client.get("/c/dairy/milk/?store=fake").content.decode()
    assert "All stores" not in html


def test_size_min_and_max_labelled(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert re.search(r'aria-label="Min [^"]+"', html) and re.search(r'aria-label="Max [^"]+"', html)
    assert 'name="size_min"' in html and 'name="size_max"' in html


def test_brand_type_label(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Brand type" in html and "<label>Brand\n" not in html


def test_favicon_vendored(client, milk_rows):
    html = client.get("/").content.decode()
    assert re.search(r'<link rel="icon" href="[^"]*favicon\.svg"', html)
    assert (CSS.parent / "favicon.svg").exists()


def test_summary_has_affordance():
    assert re.search(r"filters-drawer summary::before", CSS.read_text())


def test_price_label_visually_hidden(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert re.search(r'<span class="visually-hidden">price \$1\.00, ', html)
    assert 'aria-hidden="true"' in html
    assert ".visually-hidden" in CSS.read_text()


def test_demo_data_reports_missing_recordings(db, settings, capsys):
    settings.DEBUG = True
    call_command("demo_data")
    out = capsys.readouterr().out
    assert "/c/dairy/milk/" in out and "127.0.0.1" not in out
    assert "no recordings" in out


# ---- #33 polish ----
def test_status_node_persists_across_htmx_swaps(client, milk_rows):
    full = client.get("/c/dairy/milk/").content.decode()
    assert len(re.findall(r'id="results-status"', full)) == 1
    assert re.search(r'<p id="results-status"[^>]*role="status"', full)
    part = client.get("/c/dairy/milk/", HTTP_HX_REQUEST="true").content.decode()
    # innerHTML swap keeps the live-region node itself, so updates are announced
    assert re.search(r'<p id="results-status"[^>]*hx-swap-oob="innerHTML"[^>]*>\d+ results?</p>', part)
    assert 'role="status"' not in part


def test_status_count_includes_unranked(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    shown = html.count('class="result"')
    assert shown >= 4
    m = re.search(r'id="results-status"[^>]*>(\d+) results?<', html)
    assert int(m.group(1)) == shown
    empty = client.get("/c/dairy/milk/?size_min=99999").content.decode()
    assert re.search(r'id="results-status"[^>]*>0 results<', empty)


def test_stores_is_checkbox_group_without_selected_counter(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "<select name=\"store\"" not in html
    assert re.search(r'<input type="checkbox" name="store" value="\w+"', html)


def test_size_unit_not_pushed_without_bounds():
    js = JS.read_text()
    assert "htmx:configRequest" in js and "size_unit" in js


def test_size_inputs_not_stretched():
    assert re.search(r"\.size-range\s*\{[^}]*align-items:\s*(flex-)?start", CSS.read_text())


def test_label_check_rejects_empty_label_text():
    page = Page()
    page.feed('<label for="a"> </label><input id="a"><label><input name="b"></label>'
              '<label>Named <input name="c"></label><input aria-label=" ">')
    assert [a.get("name") or a.get("id") or "aria" for _, a in page.unlabelled()] == ["a", "b", "aria"]
