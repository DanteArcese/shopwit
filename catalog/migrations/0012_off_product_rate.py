from django.db import migrations


def _set(apps, old, new):
    apps.get_model("catalog", "SourceBudget").objects.filter(
        pk="off-product", refill_per_second=old
    ).update(refill_per_second=new)


def slow(apps, schema_editor):
    """1 per second drew 429s (issue #98); only the old default is changed, admin edits stay."""
    _set(apps, 1.0, 0.5)


def restore(apps, schema_editor):
    _set(apps, 0.5, 1.0)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0011_nutrition")]

    operations = [migrations.RunPython(slow, restore)]
