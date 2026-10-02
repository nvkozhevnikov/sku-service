#!/usr/bin/env python3
"""Interactive, GET-only launch wrapper for the Sterbrust catalog snapshot."""
from __future__ import annotations

import getpass
import os
import re
from urllib.parse import urlparse


def _validated_webhook() -> str:
    value = (os.environ.get("BITRIX_WEBHOOK_URL") or
             getpass.getpass("GET-only Bitrix webhook URL (not saved): ")).strip()
    # A messenger/browser can copy a Markdown link instead of its URL.  Accept
    # that presentation form but still validate the extracted endpoint.
    copied_urls = re.findall(r"https://[^\s<>\]\)]+", value)
    if copied_urls and len(set(copied_urls)) > 1:
        raise SystemExit("Clipboard contains different URLs; copy one webhook only.")
    if copied_urls:
        value = copied_urls[-1]
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.hostname != "sterbrust.com" or
            not re.fullmatch(r"/rest/\d+/[A-Za-z0-9_-]+/", parsed.path) or
            parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise SystemExit("Enter one HTTPS Sterbrust Bitrix REST webhook URL.")
    return value


def main() -> None:
    webhook = _validated_webhook()
    try:
        os.environ["BITRIX_WEBHOOK_URL"] = webhook
        from snapshot_sterbrust import main as snapshot_main
        snapshot_main()
    finally:
        os.environ.pop("BITRIX_WEBHOOK_URL", None)


if __name__ == "__main__":
    main()
