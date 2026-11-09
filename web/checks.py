from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def check_hosts(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    errors = []
    for name, value in (("PUBLIC_HOST", settings.PUBLIC_HOST), ("APP_HOST", settings.APP_HOST)):
        if not value or value not in settings.ALLOWED_HOSTS:
            errors.append(
                Error(
                    f"settings.{name} is unset or missing from ALLOWED_HOSTS.",
                    hint="Set UNITPRICER_PUBLIC_HOST / UNITPRICER_APP_HOST and DJANGO_ALLOWED_HOSTS.",
                    id="unitpricer.E020",
                )
            )
    if settings.PUBLIC_URL != f"https://{settings.PUBLIC_HOST}":
        errors.append(
            Error(
                "settings.PUBLIC_URL must be 'https://' + PUBLIC_HOST.",
                hint="Set UNITPRICER_PUBLIC_URL, e.g. https://unitpricer.com.",
                id="unitpricer.E021",
            )
        )
    return errors


@register(Tags.security, deploy=True)
def check_access(app_configs, **kwargs):
    if settings.DEBUG or not settings.APP_HOST:
        return []
    if settings.CF_ACCESS_AUD and settings.CF_ACCESS_TEAM_DOMAIN:
        return []
    return [
        Error(
            "APP_HOST is set but CF_ACCESS_AUD or CF_ACCESS_TEAM_DOMAIN is unset.",
            hint="Set CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD so the app host verifies Access tokens.",
            id="unitpricer.E022",
        )
    ]


@register(Tags.security, deploy=True)
def check_site_url(app_configs, **kwargs):
    """The apex serves the app, so emailed links (magic links, announcements) go there."""
    if settings.DEBUG or not settings.PUBLIC_HOST or settings.SITE_URL == settings.PUBLIC_URL:
        return []
    return [
        Error(
            "SITE_URL must equal PUBLIC_URL: emailed links point at the public host.",
            hint="Set UNITPRICER_SITE_URL to the same value as UNITPRICER_PUBLIC_URL, e.g. https://unitpricer.com.",
            id="unitpricer.E023",
        )
    ]
