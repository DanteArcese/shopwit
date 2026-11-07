from django.core.management.base import BaseCommand, CommandError

from enrichment import off


class Command(BaseCommand):
    help = "Upsert Open Food Facts rows for known product UPCs from a JSONL(.gz) file or the latest delta."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Path to a gz or plain JSONL file")
        parser.add_argument("--source", choices=["off", "opf"], default="off")
        parser.add_argument("--latest-delta", action="store_true", help="Download and sync the newest delta file")

    def handle(self, *args, **options):
        if bool(options["file"]) == bool(options["latest_delta"]):
            raise CommandError("Pass exactly one of --file or --latest-delta")
        if options["latest_delta"]:
            name, count = off.sync_latest_delta()
            self.stdout.write(f"file={name} upserted={count}")
        else:
            count = off.sync_file(options["file"], options["source"])
            self.stdout.write(f"upserted={count}")
