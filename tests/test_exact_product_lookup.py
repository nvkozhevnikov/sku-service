"""Exact detail query budget/pagination contract, no DB or network."""
from copy import deepcopy
import pytest
from universal_supplier.control_plane.store import PostgresControlPlaneStore, ProductFilters


class QueryConnection:
    def __init__(self, stable, unstable=()):
        self.stable = stable
        self.unstable = unstable
        self.queries = []

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def cursor(self): return self

    def execute(self, sql, params):
        self.queries.append((sql, params))
        self.sql = sql

    def fetchall(self):
        if 'SELECT sp.id,sp.external_id' in self.sql:
            return deepcopy(self.unstable if 'NOT sp.external_id_is_stable' in self.sql else self.stable)
        return []

    def fetchone(self):
        if 'description_text,description_html,raw_data' in self.sql:
            return {'description_text': 'Observed', 'description_html': '', 'raw_data': {}}
        return None


def store(connection):
    result = PostgresControlPlaneStore(None)
    result._connect = lambda: connection
    return result


@pytest.mark.parametrize('page_size', [25, 50, 100])
def test_exact_projection_ignores_display_pagination(page_size):
    c = QueryConnection([{'id': 6, 'external_id': '300'}])
    result = store(c)._products(ProductFilters(supplier='partner_st', page=999, page_size=page_size), exact_external_id='300')
    assert result['items'][0]['id'] == 6
    assert len(c.queries) == 1
    sql, params = c.queries[0]
    assert 'sp.external_id=%s' in sql and 'ILIKE' not in sql
    assert params == ['partner_st', '300', 2, 0]


@pytest.mark.parametrize('supplier,budget', [('partner_st', 6), ('kami', 7)])
def test_detail_has_constant_query_budget_not_neighbour_scan(supplier, budget):
    c = QueryConnection([{'id': 6, 'external_id': '300'}])
    result = store(c).product(supplier, '300')
    assert result['description_text'] == 'Observed'
    assert len(c.queries) == budget
    assert 'SELECT count(*) AS total' not in c.queries[0][0]
    assert c.queries[0][1] == [supplier, '300', 2, 0]


@pytest.mark.parametrize('count', [0, 1, 2])
def test_unstable_fallback_requires_unique_exact_identity(count):
    c = QueryConnection([], [{'id': i + 6, 'external_id': 'opaque_%_ID'} for i in range(count)])
    result = store(c).product('partner_st', 'opaque_%_ID')
    assert (result is not None) == (count == 1)
    assert len(c.queries) == (7 if count == 1 else 2)
    assert all(q[1] == ['partner_st', 'opaque_%_ID', 2, 0] for q in c.queries[:2])
    assert 'NOT sp.external_id_is_stable' in c.queries[1][0]
