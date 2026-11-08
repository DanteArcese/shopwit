import re

from catalog.models import Offer


def test_category_and_search_rows_link_to_report_form(client, milk_rows):
    for url in ("/c/dairy/milk/", "/search?q=mystery"):
        html = client.get(url).content.decode()
        rows = html.count('<li class="result')
        pks = re.findall(r'href="/feedback\?type=wrong-data&amp;offer=(\d+)" rel="nofollow">Report a problem<', html)
        assert rows and len(pks) == rows, url
        assert Offer.objects.filter(pk__in=pks).count() == len(set(pks))
