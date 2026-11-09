"""Sign-in (password or magic link), sign-out, and the account page.

No response reveals whether an email address has an account: a magic-link
request always answers "on its way" (or, for every address alike, "try again
later" when the email budget is spent), and every password failure gives the
same message. Emails and tokens are never logged.
"""

import logging
from datetime import timedelta

from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from web.net import client_ip
from web.ratelimit import allow, exceeded

from .emails import normalize_email
from .models import MagicLinkToken
from .magic import consume_token, peek_token, send_magic_link

logger = logging.getLogger(__name__)

MAGIC_EMAIL_LIMIT = (3, timedelta(minutes=15))
MAGIC_IP_LIMIT = (10, timedelta(hours=1))
SIGNIN_EMAIL_LIMIT = (5, timedelta(minutes=15))
SIGNIN_IP_LIMIT = (20, timedelta(hours=1))
DEFAULT_MAGIC_NEXT = "/lists/"
SIGNIN_ERROR = "Email or password is incorrect."
EMAIL_ERROR = "Enter a valid email address."
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _now():
    return timezone.now()


def safe_next(raw: str | None, default: str = "/") -> str:
    """A same-site path to go to after signing in; anything else is `default`."""
    raw = (raw or "").strip()
    if (
        not raw.startswith("/")
        or raw.startswith("//")
        or "\\" in raw
        or len(raw) > 500
        or not url_has_allowed_host_and_scheme(raw, allowed_hosts=set())
    ):
        return default
    return raw


def _sign_in_page(request, next_path, status=200, **context):
    return render(
        request,
        "accounts/sign_in.html",
        {"next": next_path, **context},
        status=status,
    )


@sensitive_post_parameters("password")
@never_cache
@require_http_methods(["GET", "POST"])
def sign_in(request):
    next_path = safe_next(request.POST.get("next") or request.GET.get("next"))
    if request.method == "GET":
        return _sign_in_page(request, next_path)

    email = normalize_email(request.POST.get("email"))
    password = request.POST.get("password") or ""
    ip = client_ip(request)
    if exceeded("signin-fail-ip", ip, *SIGNIN_IP_LIMIT) or (
        email is not None and exceeded("signin-fail-email", email, *SIGNIN_EMAIL_LIMIT)
    ):
        # Blocked: answer before counting anything, so a blocked client creates
        # no cache rows (and can't cull a victim's lockout by flooding).
        return _sign_in_page(request, next_path, password_error=SIGNIN_ERROR)
    user = None
    if email and password:
        # ModelBackend runs the hasher for unknown users and unusable passwords too.
        user = authenticate(request, username=email, password=password)
    if user is not None:
        login(request, user)
        return redirect(next_path)
    # Count the email only while the IP is under its limit.
    if allow("signin-fail-ip", ip, *SIGNIN_IP_LIMIT) and email is not None:
        allow("signin-fail-email", email, *SIGNIN_EMAIL_LIMIT)
    return _sign_in_page(request, next_path, password_error=SIGNIN_ERROR)


@never_cache
@require_POST
def magic_request(request):
    if not allow("magic-ip", client_ip(request), *MAGIC_IP_LIMIT):
        return render(request, "accounts/too_many.html", status=429)
    next_path = safe_next(request.POST.get("next"), DEFAULT_MAGIC_NEXT)
    email = normalize_email(request.POST.get("email"))
    if email is None:
        return _sign_in_page(request, next_path, magic_error=EMAIL_ERROR)
    if allow("magic-email", email, *MAGIC_EMAIL_LIMIT):
        try:
            sent = send_magic_link(email, next_path, _now())
        except Exception as exc:
            # The exception text can carry the address; log only its type.
            logger.error("Sign-in link email failed: %s", type(exc).__name__)
            sent = False
        if not sent:
            return render(request, "accounts/email_unavailable.html", status=503)
    return render(request, "accounts/link_sent.html")


# Pages under /account/magic/<token> must not echo the token into the footer's settings link.
_NO_NEXT = {"settings_next": "/"}


def _invalid_link(request):
    return render(request, "accounts/magic_invalid.html", _NO_NEXT)


def _user_for(email: str):
    User = get_user_model()
    try:
        user, created = User.objects.get_or_create(
            username=email, defaults={"email": email}
        )
    except IntegrityError:  # created concurrently
        user, created = User.objects.get(username=email), False
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    # Staff sign in with a password only; a mailbox alone never grants admin.
    if not user.is_active or user.is_staff or user.is_superuser:
        return None
    return user


@never_cache
@require_http_methods(["GET", "POST"])
def magic(request, token):
    now = _now()
    if request.method == "GET":
        # Mail scanners and link previews GET links; that must not use them up.
        row = peek_token(token, now)
        if row is None:
            return _invalid_link(request)
        return render(
            request,
            "accounts/magic_confirm.html",
            {"token": token, "email": row.email, **_NO_NEXT},
        )
    row = consume_token(token, now)
    if row is None:
        return _invalid_link(request)
    user = _user_for(row.email)
    if user is None:
        return _invalid_link(request)
    login(request, user, backend=MODEL_BACKEND)
    return redirect(safe_next(row.next_path, DEFAULT_MAGIC_NEXT))


@never_cache
@require_POST
def sign_out(request):
    logout(request)
    return redirect("/")


def _password_form(user, data=None):
    if user.has_usable_password():
        return PasswordChangeForm(user, data)
    return SetPasswordForm(user, data)


def _account_page(request, status=200, **context):
    context.setdefault("password_form", _password_form(request.user))
    return render(
        request,
        "accounts/account.html",
        {
            "has_password": request.user.has_usable_password(),
            "password_saved": request.GET.get("password") == "saved",
            **context,
        },
        status=status,
    )


@never_cache
@login_required
@require_http_methods(["GET"])
def account(request):
    return _account_page(request)


@sensitive_post_parameters("old_password", "new_password1", "new_password2")
@never_cache
@login_required
@require_POST
def password(request):
    form = _password_form(request.user, request.POST)
    if not form.is_valid():
        return _account_page(request, password_form=form)
    user = form.save()
    update_session_auth_hash(request, user)
    return redirect(reverse("accounts:account") + "?password=saved")


@never_cache
@login_required
@require_POST
def delete(request):
    if request.POST.get("confirm", "").strip() != "delete":
        return _account_page(request, delete_error="Type delete to confirm.")
    user = request.user
    logout(request)
    MagicLinkToken.objects.filter(email__in={user.email, user.username}).delete()
    user.delete()  # cascades to lists (and saved settings)
    return redirect("/")
