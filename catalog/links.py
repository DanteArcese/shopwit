"""Outbound link construction. Pure functions: affiliate config is passed in.

Links always go straight to the retailer or to the affiliate network's own
tracking URL, never through a UnitPricer redirect. They are built at render
time and never stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# Impact campaign path values from the `productTrackingUrl` format documented
# at walmart.io (Affiliate API docs). Verify against a real Impact link before
# going live.
WALMART_IMPACT_CAMPAIGN = "568844"
WALMART_IMPACT_AD = "9383"


def _amazon_tagged(url: str, tag: str) -> str:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    out, replaced = [], False
    for key, value in pairs:
        if key == "tag":
            if not replaced:
                out.append(("tag", tag))
                replaced = True
            continue
        out.append((key, value))
    if not replaced:
        out.append(("tag", tag))
    return urlunsplit(parts._replace(query=urlencode(out)))


def _route(offer, affiliate: Mapping[str, str | None]) -> str | None:
    """The affiliate URL for the first applicable route, else None."""
    url = offer.url
    pubid = affiliate.get("walmart_impact_publisher_id")
    if offer.retailer == "walmart" and pubid:
        return (
            f"https://goto.walmart.com/c/{pubid}/{WALMART_IMPACT_CAMPAIGN}/{WALMART_IMPACT_AD}"
            f"?veh=aff&u={quote(url, safe='')}"
        )
    tag = affiliate.get("amazon_associates_tag")
    if offer.retailer == "amazon" and tag:
        return _amazon_tagged(url, tag)
    key = affiliate.get("sovrn_api_key")
    if key:
        return f"https://sovrn.co?key={key}&u={quote(url, safe='')}"
    return None


def outbound_url(offer, affiliate: Mapping[str, str | None]) -> str:
    return _route(offer, affiliate) or offer.url


def link_attrs(offer, affiliate: Mapping[str, str | None]) -> dict:
    routed = _route(offer, affiliate)
    href = routed or offer.url
    # Defence in depth: never emit a non-http(s) href (e.g. javascript:).
    if urlsplit(href).scheme.lower() not in ("http", "https"):
        href = None
    return {
        "href": href,
        "sponsored": bool(routed),
        "rel": "sponsored noopener noreferrer" if routed else "noopener noreferrer",
        "target": "_blank",
        "ping": f"/click?o={offer.pk}",
    }


_PROGRAMS = (
    ("amazon_associates_tag", "Amazon Associates Program"),
    ("walmart_impact_publisher_id", "Walmart's affiliate program through Impact"),
    ("sovrn_api_key", "Sovrn Commerce"),
)


def affiliate_programs(affiliate: Mapping[str, str | None]) -> list[str]:
    """Display names of the programs that are configured, in a fixed order."""
    return [label for key, label in _PROGRAMS if affiliate.get(key)]
