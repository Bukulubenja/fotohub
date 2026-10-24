from django.core.management.base import BaseCommand, CommandError, CommandParser

from ops import backups


class Command(BaseCommand):
    help = "Replace the database with a backup (default: the newest). Destructive."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("key", nargs="?", help="Backup key from `backup_db --list`.")
        parser.add_argument(
            "--yes-replace-everything",
            action="store_true",
            help="Required: confirms the current data will be overwritten.",
        )

    def handle(self, *args, key: str | None, yes_replace_everything: bool, **options) -> None:
        if not yes_replace_everything:
            raise CommandError(
                "This overwrites the database. Re-run with --yes-replace-everything."
            )
        if key is None:
            available = backups.list_backups()
            if not available:
                raise CommandError("No backups found.")
            key = available[0].key
        backups.restore(key)
        self.stdout.write(self.style.SUCCESS(f"Restored from {key}"))
