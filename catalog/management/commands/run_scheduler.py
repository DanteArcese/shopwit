import logging

from django.core.management.base import BaseCommand

from catalog import scheduler


class Command(BaseCommand):
    help = "Refresh due connector/category pairs in a loop (--once for a single tick). Exits 0 on SIGTERM."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run a single tick and exit")
        parser.add_argument("--poll-seconds", type=float, default=300)

    def handle(self, *args, **options):
        # Logging is scoped to this run: handler and level are put back afterwards.
        log = scheduler.logger
        # Production logging (settings.LOGGING) already prints; attach our own only if nothing would.
        handler = None
        if not log.hasHandlers():
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            log.addHandler(handler)
        old_level = log.level
        log.setLevel(logging.INFO)
        stop = scheduler.StopEvent()  # remembers the SIGTERM time for the join deadline
        try:
            with scheduler.sigterm_stops(stop):
                scheduler.run_loop(options["poll_seconds"], once=options["once"], stop=stop)
        finally:
            if handler:
                log.removeHandler(handler)
            log.setLevel(old_level)
