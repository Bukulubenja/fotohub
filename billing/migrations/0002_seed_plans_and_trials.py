"""Seed plans, and give every existing photographer a subscription.

Paid plan prices are PLACEHOLDERS until the discovery interviews set pricing (launch plan §6);
change them in the admin. Existing accounts keep their current storage quota.
"""

import datetime

from django.db import migrations
from django.utils import timezone

GIB = 1024**3
TRIAL_DAYS = 14

PLANS = [
    # code, name, price_ugx, storage, public, order
    ("trial", "Free trial", 0, 5 * GIB, False, 0),
    ("starter", "Starter", 30_000, 25 * GIB, True, 1),
    ("pro", "Pro", 75_000, 100 * GIB, True, 2),
    ("studio", "Studio", 180_000, 400 * GIB, True, 3),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Subscription = apps.get_model("billing", "Subscription")
    User = apps.get_model("accounts", "User")
    for code, name, price, storage, public, order in PLANS:
        Plan.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "price_ugx": price,
                "period_days": 30,
                "storage_quota_bytes": storage,
                "is_public": public,
                "sort_order": order,
            },
        )
    trial = Plan.objects.get(code="trial")
    paid_until = timezone.now() + datetime.timedelta(days=TRIAL_DAYS)
    for user in User.objects.filter(subscription__isnull=True):
        Subscription.objects.create(user=user, plan=trial, paid_until=paid_until)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("billing", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
