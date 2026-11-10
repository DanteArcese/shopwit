from django.db import migrations


def _set(apps, old, new):
    apps.get_model("catalog", "SourceBudget").objects.filter(
        pk="off-product", refill_per_second=old
    ).update(refill_per_second=new)


def slow(apps, schema_editor):
    """0.5/s still drew a 429 about every 4th request; OFF lets ~15/min through (#113).
    Only the previous default is changed, admin edits stay."""
    _set(apps, 0.5, 0.25)


def restore(apps, schema_editor):
    _set(apps, 0.25, 0.5)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0013_cj_gtin_check")]

    operations = [migrations.RunPython(slow, restore)]
