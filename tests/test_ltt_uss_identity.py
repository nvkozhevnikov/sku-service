"""Regressions for the operator-confirmed source identity acceptance rule."""
import pytest
from universal_supplier.ltt_uss_identity import source_pair_relationship


def pair():
    return (dict(supplier='ltt_yug', source_id='312', requested_url='https://ltt-yug.ru/item.html',
                 final_url='https://ltt-yug.ru/item.html', title='HJD-M623A', price='4212548.06', availability='InStock'),
            dict(supplier='ltt_yug', source_id='297', requested_url='https://ltt-yug.ru/item-rub.html',
                 final_url='https://ltt-yug.ru/item-rub.html', title='HJD-M623A', price='1905000', availability='OutOfStock'))


def test_distinct_ids_keep_both_even_same_title():
    left, right = pair()
    assert source_pair_relationship(left, right) == 'DISTINCT_SOURCE_ENTITIES'
    assert (left['price'], right['price']) == ('4212548.06', '1905000')
    assert (left['availability'], right['availability']) == ('InStock', 'OutOfStock')


def test_same_id_is_source_alias():
    left, right = pair()
    right['source_id'] = left['source_id']
    assert source_pair_relationship(left, right) == 'SAME_SOURCE_ENTITY'


def test_suffix_without_id_does_not_merge():
    left, right = pair()
    left['source_id'] = right['source_id'] = None
    assert source_pair_relationship(left, right) == 'IDENTITY_UNRESOLVED_KEEP_BOTH'


def test_namespaces_never_merge():
    left, right = pair()
    right.update(supplier='ltt_msk', source_id=left['source_id'])
    assert source_pair_relationship(left, right) == 'SEPARATE_SUPPLIER_NAMESPACES'


def test_explicit_redirect_alias():
    left, right = pair()
    left['source_id'] = right['source_id'] = None
    right['final_url'] = left['final_url']
    assert source_pair_relationship(left, right) == 'EXPLICIT_REDIRECT_ALIAS'


@pytest.mark.parametrize('verified,expected', [(False, 'IDENTITY_UNRESOLVED_KEEP_BOTH'), (True, 'EXPLICIT_CANONICAL_ALIAS')])
def test_canonical_requires_verified_source_assertion(verified, expected):
    left, right = pair()
    for item in (left, right):
        item.update(source_id=None, source_canonical_url=left['requested_url'], canonical_source_verified=verified)
    assert source_pair_relationship(left, right) == expected
