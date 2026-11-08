from datetime import timedelta

from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from catalog.models import Offer
from web.net import client_ip
from web.ratelimit import allow

from .forms import TYPE_ALIASES, FeedbackForm

VISITOR_LIMIT = 5  # per client IP per hour
GLOBAL_LIMIT = 100  # saved items per day, all visitors
HONEYPOT = "hp_field_x"


def _offer_from(raw) -> Offer | None:
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None
    return Offer.objects.filter(pk=pk).select_related("store").first()


def _render_form(request, form, offer, status=200):
    return render(
        request, "feedback/form.html", {"form": form, "offer": offer}, status=status
    )


def _thanks(request):
    # The same page for every accepted outcome: saved, honeypot or repeat request.
    return render(request, "feedback/thanks.html")


@require_http_methods(["GET", "HEAD", "POST"])
def feedback(request):
    if request.method != "POST":
        offer = _offer_from(request.GET.get("offer"))
        initial = {"kind": TYPE_ALIASES.get(request.GET.get("type", ""), "")}
        if offer is not None:
            initial["offer"] = offer.pk
        return _render_form(request, FeedbackForm(initial=initial), offer)

    if not allow("feedback-ip", client_ip(request), VISITOR_LIMIT, timedelta(hours=1)):
        return render(request, "feedback/busy.html", {"rate_limited": True}, status=429)
    if request.POST.get(HONEYPOT):
        return _thanks(request)
    form = FeedbackForm(request.POST)
    if not form.is_valid():
        return _render_form(request, form, _offer_from(request.POST.get("offer")))
    if not allow("feedback-all", "global", GLOBAL_LIMIT, timedelta(days=1)):
        return render(request, "feedback/busy.html", {}, status=503)
    form.save()
    return _thanks(request)
