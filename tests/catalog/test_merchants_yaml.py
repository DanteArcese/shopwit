import re
from collections import Counter

import yaml
from django.conf import settings

PATH = settings.BASE_DIR / "catalog" / "data" / "merchants.yaml"


def _merchants():
    return yaml.safe_load(PATH.read_text())["merchants"]


def test_every_entry_is_well_formed():
    for m in _merchants():
        assert set(m) <= {"name", "retailers", "domain"}, m
        assert m["name"].strip()
        assert isinstance(m["retailers"], list)
        if "domain" in m:
            assert re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", m["domain"]), m


def test_no_retailer_code_or_domain_is_claimed_twice():
    codes = Counter(c for m in _merchants() for c in m["retailers"])
    assert [c for c, n in codes.items() if n > 1] == []
    domains = Counter(m["domain"] for m in _merchants() if "domain" in m)
    assert [d for d, n in domains.items() if n > 1] == []


def test_chains_seen_in_production_audit_are_mapped():
    # Chains the first production audit (2026-11-10) listed as unmapped (#104).
    codes = {c for m in _merchants() for c in m["retailers"]}
    for code in ["op-bjs-wholesale-club", "op-wegmans", "op-publix", "op-vons", "op-kroger",
                 "op-albertsons", "op-cvs-pharmacy", "op-dumbo-market"]:
        assert code in codes
