from django.apps import AppConfig


class BillingConfig(AppConfig):
    name = "billing"

    def ready(self) -> None:
        from billing import signals  # noqa: F401
