"""ZIPs now belong to a paid order (photo sales, 2026-10-21); gallery-wide free archives end.

Existing gallery-wide jobs are deleted first. Their files expire through the bucket's
lifecycle rule or are removed by hand; there were only development archives.
"""

import django.db.models.deletion
from django.db import migrations, models


def drop_gallery_archives(apps, schema_editor):
    apps.get_model("delivery", "DownloadJob").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("delivery", "0001_initial"),
        ("shop", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_gallery_archives, migrations.RunPython.noop),
        migrations.AddField(
            model_name="downloadjob",
            name="order",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="download_jobs",
                to="shop.order",
            ),
            preserve_default=False,
        ),
    ]
