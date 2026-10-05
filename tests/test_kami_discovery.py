from scripts.discover_kami_catalog import append_listing_evidence, traversal_url_is_safe


def test_only_main_host_ordinary_categories_and_pagination():
    assert traversal_url_is_safe('https://www.stanki.ru/catalog/machines/?PAGEN_2=2')
    assert traversal_url_is_safe('https://www.stanki.ru/bu/machines/')
    for url in (
        'https://www.stanki.ru/catalog/machines/?set_filter=y&arrFilter_8000_MIN=6000',
        'https://www.stanki.ru/catalog/machines/?PAGEN_2=2&sort=stock',
        'https://zip.stanki.ru/catalog/parts/',
        'https://www.stanki.ru:444/catalog/machines/',
        'https://www.stanki.ru/catalog/machines/#fragment',
        'https://www.stanki.ru/ajax/get/product-tab.php',
    ):
        assert not traversal_url_is_safe(url)


def test_resume_reuses_exact_listing_evidence():
    entry = {'listing_records': []}
    evidence = {'url': 'https://www.stanki.ru/catalog/a/b/', 'capture_sha256': 'a'}
    append_listing_evidence(entry, evidence)
    append_listing_evidence(entry, dict(evidence))
    assert entry['listing_records'] == [evidence]
    append_listing_evidence(entry, {**evidence, 'capture_sha256': 'b'})
    assert len(entry['listing_records']) == 2
