"""System checks for unitpricer settings."""

from datetime import timedelta

from django.conf import settings
from django.core.checks import Error, register


@register()
def check_refresh_settings(app_configs, **kwargs):
    errors = []
    refresh, cutoff = settings.REFRESH_INTERVAL, settings.FRESHNESS_CUTOFF
    for name, value in (("REFRESH_INTERVAL", refresh), ("FRESHNESS_CUTOFF", cutoff)):
        if not isinstance(value, timedelta) or value <= timedelta(0):
            errors.append(Error(f"{name} must be a positive timedelta.", id="unitpricer.E001"))
    if not errors and refresh >= cutoff:
        errors.append(
            Error(
                "REFRESH_INTERVAL must be shorter than FRESHNESS_CUTOFF, or offers expire "
                "before they are refreshed.",
                id="unitpricer.E002",
            )
        )
    return errors
