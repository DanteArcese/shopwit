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
