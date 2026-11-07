import logging

from django.core.management.base import BaseCommand

from catalog import scheduler


class Command(BaseCommand):
    help = "Refresh due connector/category pairs in a loop (--once for a single tick). Exits 0 on SIGTERM."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run a single tick and exit")
        parser.add_argument("--poll-seconds", type=float, default=300)

    def handle(self, *args, **options):
        if not logging.getLogger().handlers:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        scheduler.logger.setLevel(logging.INFO)
        should_stop = scheduler.install_sigterm_flag()
        scheduler.run_loop(options["poll_seconds"], once=options["once"], should_stop=should_stop)
