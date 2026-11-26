"""Evidence order only: decisions, identity, ranking and raw IDs stay intact."""
from dataclasses import asdict

import pytest

from sterbrust_matching.matching import Product, match_product


CASES = [
    (['147753', '51055'], ['51055', '147753']),
    (['100', '2', '11', '1'], ['1', '2', '11', '100']),
    (['9007199254740993', '9007199254740992', '2'],
     ['2', '9007199254740992', '9007199254740993']),
    (['18446744073709551616', '9223372036854775808', '1'],
     ['1', '9223372036854775808', '18446744073709551616']),
    (['02', '2', '001'], ['001', '02', '2']),
    (['0', '-1', 'x', '2', '1'], ['1', '2', '-1', '0', 'x']),
    (['٢', '10', '+2', ' 2', '2.0', '1'], ['1', '10', ' 2', '+2', '2.0', '٢']),
    (['10', '2', '2'], ['2', '2', '10']),
]


@pytest.mark.parametrize('keys,expected', CASES)
@pytest.mark.parametrize('supplier_article', [False, True])
def test_candidate_keys_numeric_evidence_order_preserves_review(keys, expected, supplier_article):
    source = Product(key='source', name='Фаскосниматель MRCM MR-R300',
                     brand='MRCM', model='MR-R300', supplier_code='partner_st',
                     supplier_article='42' if supplier_article else '')
    observations = []
    for order in (keys, list(reversed(keys)), sorted(keys)):
        candidates = [Product(key=key, name=source.name, brand=source.brand, model=source.model,
            supplier_identifiers={'partner_st': ('42',)} if supplier_article else {}) for key in order]
        before = [asdict(p) for p in candidates]
        result = match_product(source, candidates)
        assert result.status == 'REVIEW'
        assert result.method == ('DUPLICATE_SUPPLIER_ARTICLE' if supplier_article else 'NON_UNIQUE_STRONG_CANDIDATES')
        assert result.candidate_key is None and result.auto_accepted is False
        assert result.evidence['candidate_keys'] == expected
        if supplier_article:
            assert result.evidence['candidate_count'] == len(keys)
        assert [asdict(p) for p in candidates] == before
        observations.append(asdict(result))
    assert observations[0] == observations[1] == observations[2]
