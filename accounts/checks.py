from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def check_site_url(app_configs, **kwargs):
    """Emailed sign-in links use SITE_URL, never the request's Host."""
    if settings.DEBUG or settings.SITE_URL.startswith("https://"):
        return []
    return [
        Error(
            "settings.SITE_URL is unset or not https://.",
            hint="Set UNITPRICER_SITE_URL to the app origin, e.g. https://unitpricer.com.",
            id="unitpricer.E010",
        )
    ]
