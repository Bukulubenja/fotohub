from django.apps import AppConfig


class AssetsConfig(AppConfig):
    name = "assets"

    def ready(self) -> None:
        from assets import signals  # noqa: F401
