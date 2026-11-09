from django.core.checks import run_checks

from web.checks import check_site_url


def _ids(errors):
    return [e.id for e in errors]


def test_site_url_must_be_public_url(settings):
    settings.DEBUG = False
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.PUBLIC_URL = "https://unitpricer.com"
    settings.SITE_URL = "https://beta.unitpricer.com"
    assert _ids(check_site_url(None)) == ["unitpricer.E023"]
    settings.SITE_URL = "https://unitpricer.com"
    assert check_site_url(None) == []


def test_site_url_check_off_in_debug_or_without_public_host(settings):
    settings.SITE_URL = "https://beta.unitpricer.com"
    settings.PUBLIC_URL = "https://unitpricer.com"
    settings.DEBUG = True
    settings.PUBLIC_HOST = "unitpricer.com"
    assert check_site_url(None) == []
    settings.DEBUG = False
    settings.PUBLIC_HOST = ""
    assert check_site_url(None) == []


def test_site_url_check_registered_for_deploy(settings):
    settings.DEBUG = False
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.PUBLIC_URL = "https://unitpricer.com"
    settings.SITE_URL = "https://beta.unitpricer.com"
    assert "unitpricer.E023" in _ids(run_checks(include_deployment_checks=True))
    assert "unitpricer.E023" not in _ids(run_checks())
