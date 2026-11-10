import json
import logging
import time

import httpx
import yaml
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.affiliate_audit import (
    CjAdvertisers,
    Merchant,
    SovrnMerchants,
    audit,
    render_table,
    unmapped_chains,
)
from catalog.models import ClickEvent, Offer

MERCHANTS_FILE = settings.BASE_DIR / "catalog" / "data" / "merchants.yaml"
sleep = time.sleep


def build_client():
    # httpx logs every request URL at INFO; keep query strings out of any log output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return httpx.Client(timeout=20.0)


def load_merchants(path=MERCHANTS_FILE):
    data = yaml.safe_load(path.read_text())
    return [Merchant(m["name"], list(m.get("retailers") or []), m.get("domain")) for m in data["merchants"]]


def _counts(model):
    return dict(model.objects.values_list("retailer").annotate(n=Count("id")).order_by())


class Command(BaseCommand):
    help = ("Read-only report: which merchants pay through Sovrn, which are joined on CJ, "
            "and which to apply to. Never writes to the database.")

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print rows as JSON instead of a table.")
        parser.add_argument("--only", action="append", default=[], metavar="DOMAIN",
                            help="Only look up this domain (repeatable).")
        parser.add_argument("--no-cj", action="store_true", help="Skip CJ lookups.")
        parser.add_argument("--no-sovrn", action="store_true", help="Skip Sovrn lookups.")

    def handle(self, *args, **options):
        merchants = load_merchants()
        only = {d.lower().removeprefix("www.") for d in options["only"]}
        if only:
            merchants = [m for m in merchants if (m.domain or "").lower() in only]
        offer_counts = _counts(Offer)
        counts = {"offers": offer_counts, "clicks": _counts(ClickEvent)}

        secret = getattr(settings, "SOVRN_SECRET_KEY", None)
        token = getattr(settings, "CJ_PERSONAL_ACCESS_TOKEN", None)
        cid = getattr(settings, "CJ_PUBLISHER_ID", None)
        sovrn_off = "skipped" if options["no_sovrn"] else "not configured"
        cj_off = "skipped" if options["no_cj"] else "not configured"

        client = build_client()
        try:
            sovrn = cj = None
            if secret and not options["no_sovrn"]:
                sovrn = SovrnMerchants(client, secret, sleep=sleep)
            if token and cid and not options["no_cj"]:
                cj = CjAdvertisers(client, token, cid, sleep=sleep)
            rows = audit(merchants, counts, sovrn, cj, sovrn_off=sovrn_off, cj_off=cj_off)
        finally:
            client.close()

        if options["json"]:
            self.stdout.write(json.dumps([r.as_dict() for r in rows], indent=2))
            return
        for service in (cj, sovrn):
            if service is not None and service.unavailable:
                self.stdout.write(service.unavailable)
        self.stdout.write(render_table(rows))
        if not only:
            self.stdout.write("")
            self.stdout.write("Unmapped Open Prices chains (not in merchants.yaml):")
            unmapped = unmapped_chains(load_merchants(), offer_counts)
            for name, n in unmapped:
                self.stdout.write(f"  {name}: {n} offers")
            if not unmapped:
                self.stdout.write("  (none)")
