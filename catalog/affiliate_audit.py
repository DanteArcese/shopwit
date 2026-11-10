"""Affiliate merchant audit (#104): who already pays through Sovrn, who is joined on CJ.

Pure functions and small HTTP clients; no Django imports. Clients never put credentials in
anything they return: failures surface as the exception type and status code only.
"""

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import httpx
from defusedxml import ElementTree

SOVRN_URL = "https://publishers.viglink.com/api/merchant/search"
SOVRN_INTERVAL = 3.1  # Sovrn allows 1 query per 3 seconds
CJ_URL = "https://advertiser-lookup.api.cj.com/v2/advertiser-lookup"
CJ_INTERVAL = 2.6  # CJ allows 25 calls per minute
CJ_PAGE_SIZE = 100
DASH = "—"


@dataclass
class Merchant:
    name: str
    retailers: list
    domain: str | None = None


@dataclass
class SovrnResult:
    status: str  # approved | not approved | none | error | unavailable
    name: str = ""
    rate: str = ""

    def label(self):
        if self.status == "approved":
            detail = f"{self.name}, {self.rate}" if self.rate else self.name
            return f"approved: {detail}"
        if self.status in ("error", "unavailable"):
            return DASH
        return self.status


@dataclass
class CjResult:
    status: str  # joined | available | none | unavailable
    detail: str = ""

    def label(self):
        if self.status == "joined":
            return "joined" + (f": {self.detail}" if self.detail else "")
        if self.status == "available":
            return "available (not joined)" + (f": {self.detail}" if self.detail else "")
        if self.status == "unavailable":
            return DASH
        return self.status


@dataclass
class Row:
    name: str
    domain: str
    offers: int
    clicks: int
    sovrn: str
    cj: str
    action: str

    def as_dict(self):
        return asdict(self)


def describe_http_error(exc):
    """Exception type plus status code, never the URL, headers, or body."""
    text = type(exc).__name__
    response = getattr(exc, "response", None)
    if response is not None:
        text += f" {response.status_code}"
    return text


def _mask(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


class _Throttled:
    interval = 0.0

    def __init__(self, client, sleep):
        self.client = client
        self._sleep = sleep
        self._called = False
        self.unavailable = None  # one-line reason once the service failed

    def _wait(self):
        if self._called:
            self._sleep(self.interval)
        self._called = True


# --- Sovrn ---------------------------------------------------------------------------


def _countries_ok(record):
    parts = {p.strip().lower() for p in str(record.get("countries") or "").split(",")}
    return bool(parts & {"us", "all"})


def _domain_match(record, domain):
    wanted = {domain, "www." + domain}
    domains = record.get("domains")
    if domains is None:
        label = domain.split(".")[0]
        names = [str(record.get("name") or ""), str(record.get("groupName") or "")]
        return any(n.lower().startswith(label) for n in names if n)
    return any(str(d).lower() in wanted for d in domains)


def _rate_text(record):
    for rate in record.get("rates") or []:
        desc = (rate.get("description") or "").strip()
        if desc:
            lo, hi = rate.get("min"), rate.get("max")
            if lo is not None and hi is not None:
                return f"{desc} ({lo}-{hi})"
            return desc
    return ""


class SovrnMerchants(_Throttled):
    interval = SOVRN_INTERVAL

    def __init__(self, client, secret_key, sleep):
        super().__init__(client, sleep)
        self._secret = secret_key

    def lookup(self, domain):
        domain = domain.lower()
        if self.unavailable:
            return SovrnResult("unavailable")
        self._wait()
        try:
            response = self.client.get(
                SOVRN_URL, params={"domain": domain}, headers={"Authorization": f"secret {self._secret}"}
            )
            response.raise_for_status()
            records = response.json().get("merchants") or []
        except (httpx.HTTPError, ValueError) as exc:
            self.unavailable = "Sovrn unavailable: " + _mask(describe_http_error(exc), [self._secret])
            return SovrnResult("error")
        related = [r for r in records if _domain_match(r, domain)]
        for record in related:
            if record.get("approved") is True and _countries_ok(record):
                return SovrnResult("approved", str(record.get("name") or ""), _rate_text(record))
        if related:
            return SovrnResult("not approved")
        return SovrnResult("none")


# --- CJ ------------------------------------------------------------------------------


def host_of(url):
    url = (url or "").strip().lower()
    if "//" not in url:
        url = "//" + url
    host = urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _host_matches(host, domain):
    return host == domain or host.endswith("." + domain)


class CjError(Exception):
    pass


def _text(node, path):
    found = node.find(path)
    return (found.text or "").strip() if found is not None else ""


def _advertiser(node):
    return {
        "host": host_of(_text(node, "program-url")),
        "status": _text(node, "relationship-status"),
        "epc": _text(node, "three-month-epc"),
        "commission": _text(node, "actions/action/commission/default"),
    }


def _detail(adv):
    parts = []
    if adv["commission"]:
        parts.append(adv["commission"])
    if adv["epc"]:
        parts.append(f"3-mo EPC {adv['epc']}")
    return ", ".join(parts)


class CjAdvertisers(_Throttled):
    interval = CJ_INTERVAL

    def __init__(self, client, token, cid, sleep):
        super().__init__(client, sleep)
        self._token = token
        self._cid = str(cid)
        self._joined = None

    def _fail(self, message):
        message = _mask(message, [self._token])
        if self._cid:
            message = message.replace(self._cid, "<cid>")
        self.unavailable = "CJ unavailable: " + message

    def _get(self, **params):
        self._wait()
        params["requestor-cid"] = self._cid
        try:
            response = self.client.get(CJ_URL, params=params, headers={"Authorization": f"Bearer {self._token}"})
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError:
                root = None
            if root is not None and root.find("error-message") is not None:
                raise CjError(_text(root, "error-message"))
            response.raise_for_status()
            if root is None:
                raise CjError("ParseError")
        except CjError as exc:
            self._fail(str(exc))
            return None
        except httpx.HTTPError as exc:
            self._fail(describe_http_error(exc))
            return None
        advertisers = root.find("advertisers")
        if advertisers is None:
            return [], 0
        total = int(advertisers.get("total-matched") or 0)
        return [_advertiser(a) for a in advertisers.findall("advertiser")], total

    def _load_joined(self):
        joined, page = [], 1
        while True:
            result = self._get(**{"advertiser-ids": "joined", "page-number": page,
                                  "records-per-page": CJ_PAGE_SIZE})
            if result is None:
                return None
            batch, total = result
            joined.extend(batch)
            if not batch or page * CJ_PAGE_SIZE >= total:
                return joined
            page += 1

    def lookup(self, domain):
        domain = domain.lower()
        if self.unavailable:
            return CjResult("unavailable")
        if self._joined is None:
            self._joined = self._load_joined()
            if self._joined is None:
                return CjResult("unavailable")
        for adv in self._joined:
            if _host_matches(adv["host"], domain):
                return CjResult("joined", _detail(adv))
        result = self._get(**{"advertiser-ids": "notjoined", "advertiser-name": domain,
                              "records-per-page": CJ_PAGE_SIZE})
        if result is None:
            return CjResult("unavailable")
        for adv in result[0]:
            if _host_matches(adv["host"], domain):
                return CjResult("available", _detail(adv))
        return CjResult("none")


# --- Report --------------------------------------------------------------------------


def _action(domain, sovrn, cj):
    if not domain:
        return "no domain"
    sovrn_ok = sovrn is not None and sovrn.status == "approved"
    if cj is not None and cj.status == "joined":
        return "joined (CJ) — prefer over Sovrn" if sovrn_ok else "joined (CJ)"
    if sovrn_ok:
        return "covered (Sovrn)"
    if cj is not None and cj.status == "available":
        return "apply on CJ"
    return "no program found"


def audit(merchants, counts, sovrn, cj, sovrn_off="not configured", cj_off="not configured"):
    """Build report rows. A None client means that network is off, shown as `sovrn_off`/`cj_off`."""
    rows = []
    for m in merchants:
        offers = sum(counts.get("offers", {}).get(code, 0) for code in m.retailers)
        clicks = sum(counts.get("clicks", {}).get(code, 0) for code in m.retailers)
        sovrn_res = cj_res = None
        if not m.domain:
            sovrn_label = cj_label = DASH
        else:
            sovrn_res = sovrn.lookup(m.domain) if sovrn else None
            cj_res = cj.lookup(m.domain) if cj else None
            sovrn_label = sovrn_res.label() if sovrn_res else sovrn_off
            cj_label = cj_res.label() if cj_res else cj_off
        rows.append(Row(m.name, m.domain or "", offers, clicks, sovrn_label, cj_label,
                        _action(m.domain, sovrn_res, cj_res)))
    rows.sort(key=lambda r: (-r.offers, -r.clicks, r.name.lower()))
    return rows


def unmapped_chains(merchants, offer_counts):
    """Open Prices chains (`op-*`) with offers that no merchant claims: [(name, offers)]."""
    claimed = {code for m in merchants for code in m.retailers}
    found = [(code[3:].replace("-", " "), n) for code, n in offer_counts.items()
             if code.startswith("op-") and code not in claimed and n > 0]
    return sorted(found, key=lambda item: (-item[1], item[0]))


HEADERS = ["Merchant", "Domain", "Offers", "Clicks", "Sovrn", "CJ", "Action"]


def render_table(rows):
    body = [[r.name, r.domain or DASH, str(r.offers), str(r.clicks), r.sovrn, r.cj, r.action] for r in rows]
    widths = [max(len(HEADERS[i]), *(len(line[i]) for line in body)) if body else len(HEADERS[i])
              for i in range(len(HEADERS))]

    def fmt(cells):
        return " | ".join(re.sub(r"\s+$", "", c).ljust(w) for c, w in zip(cells, widths)).rstrip()

    lines = [fmt(HEADERS), "-+-".join("-" * w for w in widths)]
    lines.extend(fmt(line) for line in body)
    return "\n".join(lines)
