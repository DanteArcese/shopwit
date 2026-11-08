import smtplib
import time

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from waitlist.mail import send_budgeted, waitlist_message
from waitlist.models import Subscriber


class Command(BaseCommand):
    help = "Email the launch announcement to confirmed, not-yet-announced waitlist subscribers."

    def add_arguments(self, parser):
        parser.add_argument("--subject", required=True)
        parser.add_argument("--template", default="waitlist/email/announce.txt")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--pause", type=float, default=0.6)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        if not settings.POSTAL_ADDRESS:
            raise CommandError("UNITPRICER_POSTAL_ADDRESS is empty; commercial email needs a postal address.")
        if not settings.PUBLIC_URL and not settings.DEBUG:
            raise CommandError("UNITPRICER_PUBLIC_URL is unset; unsubscribe links would be broken.")

        now = timezone.now()
        Subscriber.purge_unconfirmed(now)
        queue = Subscriber.objects.filter(confirmed_at__isnull=False, announced_at__isnull=True).order_by(
            "confirmed_at", "id"
        )
        total = queue.count()

        def build(sub):
            return waitlist_message(
                sub,
                opts["subject"],
                opts["template"],
                {"site_url": settings.SITE_URL, "postal_address": settings.POSTAL_ADDRESS},
            )

        if opts["dry_run"]:
            self.stdout.write(f"sent 0, remaining {total}")
            first = queue.first()
            if first:
                msg = build(first)
                self.stdout.write(f"Subject: {msg.subject}\nTo: {first.email}\n\n{msg.body}")
            return

        limit = opts["limit"]
        sent = 0
        try:
            with get_connection() as connection:
                for sub in queue.iterator():
                    if limit is not None and sent >= limit:
                        break
                    if sent:
                        time.sleep(opts["pause"])
                    if not send_budgeted(
                        build(sub),
                        "announce",
                        timezone.now(),
                        reserve=settings.WAITLIST_CONFIRM_RESERVE,
                        connection=connection,
                    ):
                        break
                    sub.announced_at = timezone.now()
                    sub.save(update_fields=["announced_at"])
                    sent += 1
        except (smtplib.SMTPException, OSError) as exc:
            self.stdout.write(f"sent {sent}")
            raise CommandError(f"SMTP failure after {sent} sent: {exc}") from exc

        remaining = total - sent
        self.stdout.write(f"sent {sent}, remaining {remaining}")
        if remaining > 0 and (limit is None or sent < limit):
            self.stdout.write("budget reached; run again tomorrow")
