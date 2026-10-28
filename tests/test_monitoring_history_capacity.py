"""Regression: accumulated accepted history must remain readable without truncation."""
from contextlib import nullcontext
from pathlib import Path
import json,re
import pytest
from competitor_intel import monitoring

class Cursor:
    def __init__(self,counts):self.counts=counts;self.result=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=()):
        self.result=[]
        if 'to_regclass' in sql:self.result=[{'name':None}]
        elif 'FROM competitor_page_versions v' in sql:self.result=[{'id':i} for i in range(self.counts['versions'])]
        elif 'FROM competitor_campaign_items' in sql:self.result=[{'id':i} for i in range(self.counts['items'])]
        elif 'FROM competitor_observations' in sql:self.result=[{'id':i} for i in range(self.counts['observations'])]
        limit=re.search(r'LIMIT (\d+)',sql)
        if limit:self.result=self.result[:int(limit[1])]
    def fetchall(self):return self.result
    def fetchone(self):return self.result[0]

class Store:
    def __init__(self,counts):self.conn=self;self.counts=counts;self.closed=False
    def transaction(self):return nullcontext()
    def cursor(self):return Cursor(self.counts)
    def close(self):self.closed=True

def load(monkeypatch,tmp_path,counts):
    store=Store(counts)
    monkeypatch.setattr(monitoring,'Store',lambda _:store)
    config=tmp_path/'config.json';config.write_text(json.dumps({'dsn':'fixture'}))
    return monitoring.MonitoringRepository(config),store

def test_accumulated_history_above_previous_limits(monkeypatch,tmp_path):
    repo,store=load(monkeypatch,tmp_path,{'versions':1048,'items':24263,'observations':5304})
    state=repo.load()
    assert len(state['competitor_page_versions'])==1048
    assert len(state['competitor_campaign_items'])==24263
    assert len(state['competitor_observations'])==5304
    assert store.closed and store.read_only

@pytest.mark.parametrize('field,count',[('versions',10001),('items',200001),('observations',50001)])
def test_capacity_guard_fails_closed_without_truncation(monkeypatch,tmp_path,field,count):
    counts={'versions':1,'items':0,'observations':0};counts[field]=count
    repo,store=load(monkeypatch,tmp_path,counts)
    with pytest.raises(monitoring.MonitoringUnavailable) as error:repo.load()
    assert str(error.value.__cause__)=='READ_BOUND_EXCEEDED'
    assert store.closed
