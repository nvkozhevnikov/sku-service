"""Read three approved public sitemaps and pin a deterministic QA manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universal_supplier.full_supplier_collection import PublicPacer, discover_full_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = discover_full_manifest(pacer=PublicPacer())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest.as_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {site: sum(item.source == site for item in manifest.candidates)
              for site in ("intervesp", "beka_mak", "beka_mak_tr")}
    print(json.dumps({"sha256": manifest.sha256, "counts": counts,
                      "discovery_review": len(manifest.review_urls), "path": str(args.output)},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
