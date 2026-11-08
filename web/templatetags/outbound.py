from django import template
from django.conf import settings
from django.utils.html import format_html

from catalog.links import link_attrs

register = template.Library()


@register.simple_tag
def outbound(offer, label="View at retailer"):
    """An <a> to the retailer (or network tracking URL); autoescaped."""
    attrs = link_attrs(offer, settings.AFFILIATE)
    return format_html(
        '<a href="{}" rel="{}" target="{}" ping="{}" data-offer="{}">{}</a>',
        attrs["href"], attrs["rel"], attrs["target"], attrs["ping"], offer.pk, label,
    )
