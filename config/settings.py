import math
import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    # Empty counts as unset, so a copied .env.example with blank values works.
    return os.environ.get(name) or default


DEBUG = env("DJANGO_DEBUG", "0") == "1"

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required unless DJANGO_DEBUG=1")
    SECRET_KEY = "insecure-dev-key-not-for-production"

SECRET_KEY_FALLBACKS = [k.strip() for k in env("DJANGO_SECRET_KEY_FALLBACKS", "").split(",") if k.strip()]

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "catalog",
    "web",
    "waitlist",
    "feedback",
    "lists",
    "accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "web.hosts.HostRoutingMiddleware",
    "web.access.CloudflareAccessMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.context_processors.prefs",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

_database_url = env("DATABASE_URL")
# Unset DATABASE_URL leaves Django on its dummy backend; DB access then fails loudly.
DATABASES = {"default": dj_database_url.parse(_database_url) if _database_url else {}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = env("DJANGO_STATIC_ROOT", None) or BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# unitpricer
REFRESH_INTERVAL = timedelta(hours=float(env("UNITPRICER_REFRESH_HOURS", 12)))
FRESHNESS_CUTOFF = timedelta(hours=float(env("UNITPRICER_FRESHNESS_HOURS", 24)))
SITE_CURRENCY = env("UNITPRICER_CURRENCY", "USD")
# Open Prices: dated in-store shelf prices, shown as "observed" offers.
OBSERVED_MAX_AGE = timedelta(days=float(env("UNITPRICER_OBSERVED_MAX_AGE_DAYS", 90)))
OPEN_PRICES_ENABLED = env("UNITPRICER_OPEN_PRICES", "0") == "1"
OPEN_PRICES_COUNTRY = env("UNITPRICER_OPEN_PRICES_COUNTRY", "US")
OPEN_PRICES_INTERVAL = timedelta(hours=float(env("UNITPRICER_OPEN_PRICES_HOURS", 24)))
LIVE_OFF_ENABLED = env("UNITPRICER_LIVE_OFF", "0") == "1"


def _fetch_threads(raw):
    """1 or 2 worker fetch threads; unset or blank is 1, a non-integer is an error."""
    raw = (raw or "").strip()
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(
            f"UNITPRICER_FETCH_THREADS must be an integer, got {raw!r}"
        ) from None
    return max(1, min(2, value))


FETCH_THREADS = _fetch_threads(os.environ.get("UNITPRICER_FETCH_THREADS"))
WARM_INTERVAL = timedelta(hours=float(env("UNITPRICER_WARM_HOURS", 12)))
LABELS_FILE = BASE_DIR / "data/labels.yaml"


def _budget(key, capacity, refill, daily):
    prefix = "UNITPRICER_BUDGET_" + key.upper().replace("-", "_")
    # Unset or blank: the default cap. "unlimited" (any case): no cap. Anything else must be an integer.
    raw_daily = (os.environ.get(prefix + "_DAILY_LIMIT") or "").strip()
    if not raw_daily:
        daily_limit = daily
    elif raw_daily.lower() == "unlimited":
        daily_limit = None
    else:
        try:
            daily_limit = int(raw_daily)
            if daily_limit <= 0:
                raise ValueError
        except ValueError:
            raise ImproperlyConfigured(
                f"{prefix}_DAILY_LIMIT must be a positive integer or 'unlimited', got {raw_daily!r}"
            ) from None
    raw_capacity = env(prefix + "_CAPACITY", capacity) or capacity
    try:
        capacity = float(raw_capacity)
        # A bucket below 1 token never grants one; inf would never run dry; nan compares false.
        if not (math.isfinite(capacity) and capacity >= 1):
            raise ValueError
    except ValueError:
        raise ImproperlyConfigured(
            f"{prefix}_CAPACITY must be a finite number of at least 1, got {raw_capacity!r}"
        ) from None
    raw_refill = env(prefix + "_REFILL_PER_SECOND", refill) or refill
    try:
        refill_per_second = float(raw_refill)
        if not (math.isfinite(refill_per_second) and refill_per_second >= 0):
            raise ValueError
    except ValueError:
        raise ImproperlyConfigured(
            f"{prefix}_REFILL_PER_SECOND must be a finite number of at least 0, got {raw_refill!r}"
        ) from None
    return {
        "capacity": capacity,
        "refill_per_second": refill_per_second,
        "daily_limit": daily_limit,
    }


SOURCE_BUDGETS = {
    "off-search": _budget("off-search", 10, 10 / 60, None),
    # Capacity 1: no burst, a steady 1 req/s, the etiquette the daily sync always kept.
    "openprices": _budget("openprices", 1, 1.0, None),
    "walmart": _budget("walmart", 5, 1.0, 5000),
}
OPEN_PRICES_INCLUDE_ASSISTED = env("UNITPRICER_OPEN_PRICES_ASSISTED", "1") != "0"
AFFILIATE = {
    "walmart_impact_publisher_id": env("WALMART_IMPACT_PUBLISHER_ID"),
    "sovrn_site_key": env("SOVRN_SITE_KEY"),
    "amazon_associates_tag": env("AMAZON_ASSOCIATES_TAG"),
}
# Feedback sync: a fine-grained token limited to issues on this repo. Unset = sync off.
GITHUB_TOKEN = env("UNITPRICER_GITHUB_TOKEN", "")
GITHUB_REPO = env("UNITPRICER_GITHUB_REPO", "DanteArcese/unitpricer")
CATEGORIES_DIR = BASE_DIR / "categories"
STORE_BRANDS_FILE = BASE_DIR / "data/store_brands.yaml"

SESSION_COOKIE_SAMESITE = "Lax"

# Hosts and origins. Empty PUBLIC_HOST/APP_HOST means no host routing (dev).
PUBLIC_HOST = env("UNITPRICER_PUBLIC_HOST", "")
APP_HOST = env("UNITPRICER_APP_HOST", "")
PUBLIC_URL = env("UNITPRICER_PUBLIC_URL", "")
SITE_URL = env("UNITPRICER_SITE_URL", "")
POSTAL_ADDRESS = env("UNITPRICER_POSTAL_ADDRESS", "")
TRUST_CF_IP = env("UNITPRICER_TRUST_CF_IP", "0") == "1"
# Cloudflare Access (app host). Empty AUD disables the token check (dev).
CF_ACCESS_TEAM_DOMAIN = env("CF_ACCESS_TEAM_DOMAIN", "")
CF_ACCESS_AUD = env("CF_ACCESS_AUD", "")

# Security (production sits behind Cloudflare Tunnel, which terminates TLS).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", 0 if DEBUG else 3600))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
# Preload is a separate, later decision.
SECURE_HSTS_PRELOAD = False
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 24 * 60
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in env(
        "DJANGO_CSRF_TRUSTED_ORIGINS", ",".join(f"https://{h}" for h in ALLOWED_HOSTS)
    ).split(",")
    if o.strip()
]

# Email (production: Resend over STARTTLS on port 2587; DigitalOcean blocks 25/465/587).
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("DJANGO_EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("DJANGO_EMAIL_PORT", 587))
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD") or env("RESEND_API_KEY", "")
EMAIL_USE_TLS = env("DJANGO_EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "UnitPricer <hello@unitpricer.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Send budget: rolling 24 h cap across all kinds (Resend free tier); announcements
# leave WAITLIST_CONFIRM_RESERVE of it for confirmations.
DAILY_EMAIL_CAP = int(env("UNITPRICER_DAILY_EMAIL_CAP", 100))
WAITLIST_CONFIRM_RESERVE = 20
WAITLIST_CONFIRM_MAX_AGE = timedelta(days=7)

# Accounts: emailed sign-in links use SITE_URL (never the request's Host).
MAGIC_LINK_TTL = timedelta(minutes=15)
LOGIN_URL = "accounts:sign_in"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "unitpricer_cache",
        # Culling (a third of the rows once full) would reset rate-limit lockouts;
        # keep the cap far above what rate-limited traffic can create.
        "OPTIONS": {"MAX_ENTRIES": 100_000},
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    # Token paths (waitlist, magic links) are redacted from every record.
    "filters": {"redact": {"()": "web.glogging.RedactFilter"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "plain",
            "filters": ["redact"],
        }
    },
    "root": {"handlers": ["stdout"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"level": "ERROR", "propagate": True},
        # Bots probing the tunnel with odd Host headers.
        "django.security.DisallowedHost": {"level": "CRITICAL", "propagate": True},
    },
}
