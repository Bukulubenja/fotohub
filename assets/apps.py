from django.apps import AppConfig


class AssetsConfig(AppConfig):
    name = "assets"

    def ready(self) -> None:
        from assets import signals, watermark  # noqa: F401  (watermark registers a check)
