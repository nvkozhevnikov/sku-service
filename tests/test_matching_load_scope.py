from contextlib import nullcontext
from universal_supplier.identity_service import _load_cards

class Cursor:
    def __init__(self):self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,q,params):self.calls.append((q,params))
    def fetchall(self):return []
class Connection:
    def __init__(self):self.q=Cursor()
    def cursor(self):return self.q

def test_bounded_source_filter_is_bound_not_interpolated():
    c=Connection();assert _load_cards(c,'partner_st',external_ids=['296',"' OR true",'296'])==([], {})
    q,params=c.q.calls[0]
    assert 'sp.external_id=ANY(%s)' in q and params==('partner_st',["' OR true",'296'])
    assert "' OR true" not in q

def test_empty_batch_never_loads_full_source():
    c=Connection();assert _load_cards(c,'partner_st',external_ids=[])==([], {}) and not c.q.calls

def test_full_call_preserves_original_query_universe():
    c=Connection();_load_cards(c,'partner_st')
    q,params=c.q.calls[0];assert 'sp.external_id=ANY' not in q and params==('partner_st',)
