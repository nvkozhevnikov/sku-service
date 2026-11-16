"""Historical runs lacking coverage must not erase the read-only monitoring view."""
import pytest
from competitor_intel.business_data import normalized_dataset

@pytest.mark.parametrize('result',[None,{}, {'coverage':None},{'coverage':[]}])
def test_unknown_coverage_remains_unknown_without_crashing_projection(result):
    state={key:[] for key in ('competitor_sources','competitor_pages',
        'competitor_page_versions','competitor_campaign_items','competitor_observations','competitor_ai_analysis')}
    state['competitor_runs']=[{'id':61,'result':result}]
    data=normalized_dataset(state)
    assert data['coverage']==[]
    assert data['current']==[] and data['history']==[]
    assert data['ai_network_calls']==0

def test_null_coverage_does_not_hide_saved_news_and_blog():
    from tests.test_competitor_news_ui import content_state,service
    state=content_state()
    state['competitor_ai_analysis']=[]
    known_coverage=normalized_dataset(state)['coverage']
    state['competitor_runs'].append({'id':61,'result':{'coverage':None}})
    assert normalized_dataset(state)['coverage']==known_coverage
    facts=service(state).facts()
    assert len(facts['news'])==2
    assert len(facts['blog'])==1
