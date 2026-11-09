import shutil
from pathlib import Path

import pytest

from catalog.categories_sync import SyncResult, sync_categories
from catalog.models import AttributeDef, Category

REPO_CATEGORIES = Path(__file__).resolve().parents[2] / "categories"


@pytest.fixture
def root(tmp_path):
    dest = tmp_path / "categories"
    shutil.copytree(REPO_CATEGORIES, dest)
    return dest


@pytest.mark.django_db
def test_sync_creates_all_repo_categories():
    result = sync_categories(REPO_CATEGORIES)
    assert result == SyncResult(created=16, updated=0, deactivated=0)
    milk = Category.objects.get(slug="dairy/milk")
    assert milk.search[0] == "2% milk"
    assert milk.dimension == "volume"
    assert (
        Category.objects.get(slug="household/toilet-paper").parent_slug
        == "household/paper-goods"
    )


@pytest.mark.django_db
def test_sync_is_idempotent():
    sync_categories(REPO_CATEGORIES)
    assert sync_categories(REPO_CATEGORIES) == SyncResult(
        created=0, updated=0, deactivated=0
    )


@pytest.mark.django_db
def test_removed_category_deactivated_not_deleted(root):
    sync_categories(root)
    (root / "dairy" / "eggs.yaml").unlink()
    assert sync_categories(root) == SyncResult(created=0, updated=0, deactivated=1)
    eggs = Category.objects.get(slug="dairy/eggs")
    assert eggs.active is False
    assert eggs.attribute_defs.exists()
    assert Category.objects.filter(active=True).count() == 15
    # second run: nothing newly deactivated
    assert sync_categories(root).deactivated == 0
    # reappearing reactivates and counts as updated
    shutil.copy(REPO_CATEGORIES / "dairy" / "eggs.yaml", root / "dairy" / "eggs.yaml")
    assert sync_categories(root) == SyncResult(created=0, updated=1, deactivated=0)
    assert Category.objects.get(slug="dairy/eggs").active is True


@pytest.mark.django_db
def test_attribute_defs_replaced(root):
    sync_categories(root)
    names = set(
        AttributeDef.objects.filter(category__slug="dairy/milk").values_list(
            "name", flat=True
        )
    )
    assert {"fat", "organic"} <= names
    fat = AttributeDef.objects.get(category__slug="dairy/milk", name="fat")
    assert fat.type == "enum"
    assert fat.values == ["skim", "1", "2", "whole"]

    path = root / "dairy" / "milk.yaml"
    text = path.read_text()
    path.write_text(text.replace("  lactose_free:", "  lactose_free_renamed:"))
    assert sync_categories(root).updated == 1
    names = set(
        AttributeDef.objects.filter(category__slug="dairy/milk").values_list(
            "name", flat=True
        )
    )
    assert "lactose_free" not in names
    assert "lactose_free_renamed" in names


@pytest.mark.django_db
def test_sync_categories_command_prints_counts(capsys):
    from django.core.management import call_command

    call_command("sync_categories")
    assert "created=16 updated=0 deactivated=0" in capsys.readouterr().out
