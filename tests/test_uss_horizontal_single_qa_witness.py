from scripts import qa_uss_horizontal_single_witness as h


def test_saved_fixed_kit_is_exactly_one_orderable_source_product():
    with h.q.q.guard() as network:fact=h.witness()
    assert fact['single_orderable_row'] and not fact['variant_matrix']
    assert fact['expected']['article']=='510 0004' and fact['expected']['price']=='1440'
    assert fact['expected']['currency']=='RUB' and fact['expected']['availability']=='PreOrder'
    assert not any(network.values())


def test_single_fixed_kit_with_explicit_own_commercial_row_must_not_be_omitted():
    with h.q.q.guard():fact=h.witness()
    assert fact['actual']['gate_status']=='SUCCESS', (
        'Single kit with selfcanonical, one article/price/stock/order row falsely AMBIGUOUS: '+repr(fact['actual']))
    assert fact['actual']['product']['price']['current_price']==1440
    assert fact['actual']['product']['price']['currency']=='RUB'
    assert fact['actual']['product']['availability']=='PreOrder'
