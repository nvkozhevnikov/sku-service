"""Bounded GET-only access/rate check. No listings, database writes or retries."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.http_capture import UrllibPublicHttpClient, capture_public_html, CaptureStatus
from universal_supplier.intervesp_listing import product_url, bounded_rate_plan


class DetailClient:
    def __init__(self): self.client = UrllibPublicHttpClient()
    def get(self, url, *, timeout_seconds):
        product_url(url)
        return self.client.get_validated(url, timeout_seconds=timeout_seconds, validator=product_url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Preserve existing rate checkpoint')
    args.output.mkdir(parents=True)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'http_gets': 0, 'rows': [], 'stage': 'ACCESS_CHECK'}
    def flush():
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    flush()
    client = UrllibPublicHttpClient()
    try:
        robots_url = 'https://intervesp.ru/robots.txt'
        response = client.get_validated(robots_url, timeout_seconds=20,
            validator=lambda url: None if url == robots_url else (_ for _ in ()).throw(ValueError('Unsafe robots redirect')))
        report['http_gets'] += 1
        report['robots_http_status'] = response.status_code
        if response.status_code != 200: raise RuntimeError('Robots access unverified: STOP')
        text = response.body.decode('utf-8', 'strict')
        if any(marker in text.lower() for marker in ('captcha', 'cf-chl-', '<html')):
            raise RuntimeError('Robots challenge/non-robots content: STOP')
        robots = RobotFileParser(); robots.parse(text.splitlines())
        agent = 'UniversalSupplierReadOnly/1.0'
        delay = robots.crawl_delay(agent)
        rate = bounded_rate_plan(float(delay or 0))
        report['rate_policy'] = rate
        report['robots_sha256'] = hashlib.sha256(response.body).hexdigest()
        (args.output / 'robots.txt').write_bytes(response.body)
        assessment = json.loads((ROOT / 'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/OFFLINE_ASSESSMENT.json').read_text(encoding='utf-8'))
        chosen = [row for row in assessment['detail_get_plan'] if not row['old_detail_available']][:2]
        for number, row in enumerate(chosen):
            url = product_url(row['url'])
            if not robots.can_fetch(agent, url): raise RuntimeError('Robots disallows detail URL: STOP')
            pause = rate['safe_seconds'] if number == 0 else rate['faster_seconds']
            time.sleep(pause)  # after previous response, never parallel
            started = time.monotonic()
            capture = capture_public_html(url, evidence_dir=args.output / 'evidence', client=DetailClient(),
                timeout_seconds=20, max_attempts=1)
            report['http_gets'] += 1
            report['rows'].append({'url': url, 'pause_seconds': pause, 'http_status': capture.http_status,
                'status': capture.status.value, 'seconds': time.monotonic()-started,
                'diagnostics': capture.diagnostics, 'evidence_ref': capture.evidence_ref,
                'capture': asdict(capture.capture) if capture.capture else None})
            flush()
            if capture.status != CaptureStatus.SUCCESS:
                raise RuntimeError('Rate/access sample failed; STOP without retries or bypass')
        report['stage'] = 'BOUNDED_RATE_PASS'
        report['faster_live_mode_deferred'] = not rate['faster_distinct_permitted']
    except Exception as error:
        report.update(stage='STOPPED_ACCESS_OR_RATE', error=str(error))
    report['finished_at'] = datetime.now(timezone.utc).isoformat(); flush()
    print(json.dumps(report, default=str))


if __name__ == '__main__': main()
