"""Optional saved-version analysis; scraped input is untrusted data."""
from dataclasses import dataclass, asdict, field
from typing import Protocol
from psycopg.types.json import Jsonb
from .store import json_value

CONTENT_TYPES = {'PROMOTION','PRICE_CUT','CLEARANCE','NEW_PRODUCT','NEW_PRODUCT_LINE','ASSORTMENT_EXPANSION','FINANCING','INSTALLMENT','EVENT','EXHIBITION','SEASONAL','CONTENT_MARKETING','OTHER'}

@dataclass
class AnalysisResult:
    analysis_status: str = 'NOT_REQUESTED'
    summary: str | None = None
    content_type: str | None = None
    topics: list = field(default_factory=list)
    products: list = field(default_factory=list)
    commercial_mechanic: list = field(default_factory=list)
    strategic_signals: list = field(default_factory=list)
    sterbrust_relevance: str | None = None
    reason: str | None = None

class Analyzer(Protocol):
    provider: str
    model: str
    prompt_version: str
    def analyze(self, saved_input: dict) -> AnalysisResult: ...

class NullAnalyzer:
    provider = 'none'; model = 'none'; prompt_version = 'v1'
    def analyze(self, saved_input): return AnalysisResult()

def analyze_pending(store, analyzer=None):
    analyzer = analyzer or NullAnalyzer()
    versions = store.query('''SELECT v.id,v.content_hash,v.payload FROM competitor_page_versions v WHERE v.qa_status <> 'REJECTED_EXTRACTOR' AND NOT EXISTS
                             (SELECT 1 FROM competitor_ai_analysis a WHERE a.page_version_id=v.id AND a.provider=%s AND a.model=%s AND a.prompt_version=%s AND a.analysis_status IN ('DONE','NOT_REQUESTED')) ORDER BY v.id''',
                           (analyzer.provider,analyzer.model,analyzer.prompt_version))
    result_counts = {'DONE':0,'ERROR':0,'NOT_REQUESTED':0,'ai_network_calls':0 if analyzer.provider=='none' else 'provider-owned'}
    for v in versions:
        result = None; error = None
        try:
            # Providers receive saved content only, never capabilities/commands.
            result = analyzer.analyze(v['payload'])
            if result.analysis_status not in ('DONE','NOT_REQUESTED'): raise ValueError('Invalid final analysis status')
            if result.analysis_status == 'DONE' and (result.content_type not in CONTENT_TYPES or result.sterbrust_relevance not in ('HIGH','MEDIUM','LOW')): raise ValueError('Invalid structured analysis')
        except Exception as e:
            error = type(e).__name__; result = AnalysisResult(analysis_status='ERROR')
        raw = json_value(asdict(result)); result_counts[result.analysis_status] += 1
        with store.conn.transaction(), store.conn.cursor() as c:
            c.execute('''INSERT INTO competitor_ai_analysis(page_version_id,content_hash,provider,model,prompt_version,analysis_status,summary,content_type,topics,products_json,commercial_mechanic_json,strategic_signals_json,sterbrust_relevance,reason,raw_structured_result,analyzed_at,error)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                         ON CONFLICT(page_version_id,provider,model,prompt_version) DO UPDATE SET analysis_status=excluded.analysis_status,summary=excluded.summary,content_type=excluded.content_type,topics=excluded.topics,products_json=excluded.products_json,commercial_mechanic_json=excluded.commercial_mechanic_json,strategic_signals_json=excluded.strategic_signals_json,sterbrust_relevance=excluded.sterbrust_relevance,reason=excluded.reason,raw_structured_result=excluded.raw_structured_result,analyzed_at=excluded.analyzed_at,error=excluded.error''',
                      (v['id'],v['content_hash'],analyzer.provider,analyzer.model,analyzer.prompt_version,result.analysis_status,result.summary,result.content_type,Jsonb(result.topics),Jsonb(result.products),Jsonb(result.commercial_mechanic),Jsonb(result.strategic_signals),result.sterbrust_relevance,result.reason,Jsonb(raw),error))
    return result_counts
