from django.core.management.base import BaseCommand, CommandParser

from ops import backups


class Command(BaseCommand):
    help = "Back up the database to object storage now, or list the backups."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--list", dest="show_list", action="store_true", help="List backups, newest first."
        )

    def handle(self, *args, show_list: bool, **options) -> None:
        if show_list:
            for backup in backups.list_backups():
                self.stdout.write(f"{backup.key}  {backup.size:,} bytes")
            return
        backup = backups.take_backup()
        self.stdout.write(self.style.SUCCESS(f"Backed up to {backup.key} ({backup.size:,} bytes)"))
