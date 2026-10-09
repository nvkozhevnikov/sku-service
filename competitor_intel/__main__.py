import argparse
from datetime import date
import json
from pathlib import Path
from .model import SOURCES
from .store import Store
from .runner import crawl
from .report import report
from .analyzer import analyze_pending

def main():
    p = argparse.ArgumentParser(description='Изолированный мониторинг конкурентов; AI disabled by default')
    p.add_argument('--dev-config',type=Path,help='Ignored local JSON with dedicated DB dsn; otherwise COMPETITOR_INTEL_DSN')
    sub = p.add_subparsers(dest='command',required=True)
    sub.add_parser('init-db')
    c = sub.add_parser('crawl'); group=c.add_mutually_exclusive_group(required=True)
    group.add_argument('--source',choices=SOURCES); group.add_argument('--all',action='store_true')
    c.add_argument('--since',type=date.fromisoformat,default=date(2026,9,1)); c.add_argument('--through',type=date.fromisoformat,default=date.today())
    c.add_argument('--max-pages',type=int,default=45); c.add_argument('--browser',action='store_true')
    c.add_argument('--output',default='reports/COMPETITOR_INTELLIGENCE_2026-10-09')
    r = sub.add_parser('report'); r.add_argument('--since',type=date.fromisoformat,default=date(2026,9,1)); r.add_argument('--through',type=date.fromisoformat,default=None); r.add_argument('--output',default=None)
    r.add_argument('--preview',action='store_true',help='Read-only offline HTML/CSV/JSON business report; no crawl/AI')
    r.add_argument('--ending-days',type=int,default=7)
    a = sub.add_parser('analyze'); a.add_argument('--provider',choices=['none'],default='none'); a.add_argument('--pending',action='store_true')
    args = p.parse_args()
    dsn = json.loads(args.dev_config.read_text(encoding='utf-8'))['dsn'] if args.dev_config else None
    store = Store(dsn)
    try:
        if args.command=='init-db': store.migrate(); result={'database':'sterbrust_competitor_intel','schema':'public','migration':'DONE'}
        elif args.command=='crawl':
            if not 1<=args.max_pages<=100: p.error('max-pages must be 1..100')
            if args.source=='kuvalda_nnov':
                from .kuvalda_public_runner import crawl_public
                result=crawl_public(store,args.output,args.since,args.through,min(args.max_pages,60))
            elif args.all:
                from .kuvalda_public_runner import crawl_public
                kuvalda=crawl_public(store,args.output,args.since,args.through,min(args.max_pages,60))
                metalmaster=crawl(store,['metalmaster'],args.since,args.through,args.output,args.max_pages,args.browser)
                result={'kuvalda':kuvalda,'metalmaster':metalmaster}
            else:
                result=crawl(store,[args.source],args.since,args.through,args.output,args.max_pages,args.browser)
        elif args.command=='report':
            if args.preview:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                from .business_report import preview_from_store
                today=args.through or datetime.now(ZoneInfo('Europe/Moscow')).date()
                result=preview_from_store(store,args.since,today,args.output or f'reports/COMPETITOR_BUSINESS_REPORT_PREVIEW_{today}',args.ending_days)
            else: result=report(store,args.since,args.through or date.today(),args.output or 'reports/COMPETITOR_INTELLIGENCE_2026-10-09')
        else: result=analyze_pending(store)
        print(json.dumps(result,ensure_ascii=False,default=str))
    finally: store.close()

if __name__=='__main__': main()
