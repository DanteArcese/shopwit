"""Route lists shared by test_hosts.py and test_access.py (one source of truth)."""

PUBLIC = "unitpricer.com"
APP = "beta.unitpricer.com"

# Every non-admin page the apex serves to anyone, incl. M4's lists and account pages and
# the HTMX partial of the category page. Each answers 200 without a token.
APEX_ROUTES = [
    ("get", "/", {}),
    ("get", "/c/dairy/milk/", {}),
    ("get", "/c/dairy/milk/", {"HTTP_HX_REQUEST": "true"}),
    ("get", "/search?q=lemonade", {}),
    ("get", "/settings", {}),
    ("get", "/disclosure", {}),
    ("get", "/feedback", {}),
    ("get", "/privacy", {}),
    ("get", "/lists/", {}),
    ("get", "/account/sign-in", {}),
]

# Everything that must be refused on beta (and any non-public host) without a valid token.
BETA_ROUTES = APEX_ROUTES + [
    ("get", "/admin/", {}),
    ("get", "/admin/login/", {}),
    ("post", "/admin/login/", {}),
    ("post", "/waitlist/", {}),
    ("get", "/waitlist/confirm/" + "x" * 40, {}),
    ("post", "/waitlist/confirm/" + "x" * 40, {}),
    ("post", "/settings", {}),
    ("post", "/click", {}),
    ("post", "/feedback", {}),
    ("get", "/lists/add?category=dairy%2Fmilk", {}),
    ("post", "/lists/new", {}),
    ("get", "/account/", {}),
    ("get", "/account/password", {}),
    ("post", "/account/password", {}),
    ("post", "/account/sign-in", {}),
    ("post", "/account/magic-link", {}),
    ("get", "/robots.txt", {}),
]
