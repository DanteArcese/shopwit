from django import template
from django.conf import settings
from django.utils.html import format_html

from catalog.links import link_attrs

register = template.Library()


@register.simple_tag
def outbound(offer, label="View at retailer"):
    """An <a> to the retailer (or network tracking URL); autoescaped."""
    attrs = link_attrs(offer, settings.AFFILIATE)
    if not attrs["href"]:
        return ""
    link = format_html(
        '<a href="{}" rel="{}" target="{}" ping="{}" data-offer="{}">{}</a>',
        attrs["href"], attrs["rel"], attrs["target"], attrs["ping"], offer.pk, label,
    )
    if attrs["sponsored"]:
        return format_html('{} <span class="affiliate-mark">(affiliate link)</span>', link)
    return link
