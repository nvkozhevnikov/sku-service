"""GET-only canonical snapshot into a new KAMI evidence directory.

Webhook is process-only; never written to files or printed. The previous
canonical registry/release inputs remain untouched.
"""
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import snapshot_sterbrust as snapshot

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/CANONICAL_CURRENT'


def main():
    value = os.environ.get('BITRIX_WEBHOOK_URL', '')
    uri = urlsplit(value)
    if uri.scheme != 'https' or uri.hostname != 'sterbrust.com' or uri.query or uri.fragment:
        raise RuntimeError('GET-only Sterbrust webhook required; endpoint not printed')
    if OUT.exists():
        raise RuntimeError('Canonical output already exists; verify existing snapshot, never overwrite it')
    snapshot.REPORTS = OUT
    snapshot.REST_DIR = OUT / 'rest'
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sys.argv = ['snapshot_kami_canonical', '--workers', '2']
    try:
        snapshot.main()
    finally:
        os.environ.pop('BITRIX_WEBHOOK_URL', None)


if __name__ == '__main__':
    main()
