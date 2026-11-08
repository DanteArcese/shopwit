import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def prod_env():
    """Environment for a production-like settings subprocess. Extend with overrides."""
    base = {
        k: v for k, v in os.environ.items() if k.startswith(("PATH", "HOME", "LANG", "PYTHON", "VIRTUAL"))
    }
    return {
        **base,
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(50)[:50],
        "DJANGO_ALLOWED_HOSTS": "unitpricer.com,www.unitpricer.com,beta.unitpricer.com",
        "UNITPRICER_PUBLIC_HOST": "unitpricer.com",
        "UNITPRICER_APP_HOST": "beta.unitpricer.com",
        "UNITPRICER_PUBLIC_URL": "https://unitpricer.com",
        "UNITPRICER_SITE_URL": "https://beta.unitpricer.com",
        "CF_ACCESS_TEAM_DOMAIN": "unitpricer.cloudflareaccess.com",
        "CF_ACCESS_AUD": "test-aud",
        "DATABASE_URL": "postgres://x:y@localhost:5432/z",
    }


@pytest.fixture
def run_prod():
    """Run `python <args>` with the given env from the repo root; returns CompletedProcess."""

    def run(env, *args):
        return subprocess.run(
            [sys.executable, *args], env=env, cwd=ROOT, capture_output=True, text=True
        )

    return run
