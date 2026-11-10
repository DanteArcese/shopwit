import pytest

pytestmark = pytest.mark.django_db


def test_home_has_impact_site_verification_meta(client):
    html = client.get("/").content.decode()
    assert (
        "<meta name='impact-site-verification' value='c0cd4ae8-db8e-4896-a0b2-3f4ee8a96eb7'>"
        in html
    )
