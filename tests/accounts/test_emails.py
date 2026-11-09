import pytest

from accounts.emails import normalize_email


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Alice@Example.COM ", "alice@example.com"),
        ("a@b.co", "a@b.co"),
        ("not-an-email", None),
        ("", None),
        (None, None),
        ("a" * 140 + "@example.com", None),  # 152 chars > User.username's 150
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected
