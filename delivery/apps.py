from django.apps import AppConfig


class DeliveryConfig(AppConfig):
    name = "delivery"

    def ready(self) -> None:
        from delivery import signals  # noqa: F401
