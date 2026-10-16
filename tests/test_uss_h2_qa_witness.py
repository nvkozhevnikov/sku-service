from scripts import qa_uss_h2_witness as h


def test_exact_saved_hswh5_card_is_one_unambiguous_product():
    with h.q.guard() as network:fact=h.witness()
    assert fact['single_own_commercial_table_count']==1
    assert fact['source_title']=='Бутылочный домкрат HSWH 5.'
    assert fact['model']=='HSWH 5' and fact['article']=='620 1001'
    assert fact['expected_price']==2580 and fact['expected_currency']=='RUB'
    assert network['prohibited_transport_attempts']==0


def test_explicit_h2_own_product_must_not_be_falsely_ambiguous():
    with h.q.guard():fact=h.witness()
    assert fact['actual_gate_status']=='SUCCESS', (
        'Explicit own h2.zagolovok_u title+single model/article/price/stock/order table ignored: '+
        repr({k:fact[k] for k in ('index','source_id','source_title','actual_title','actual_gate_status','actual_gate_diagnostics','evidence_sha256')}))
    assert fact['actual_title']==fact['source_title']
