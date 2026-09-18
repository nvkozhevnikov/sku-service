from __future__ import annotations

import argparse
import asyncio

from .adapter import OptimumAdapter


def main():
    parser = argparse.ArgumentParser(description="Read-only optimum.su supplier adapter")
    parser.add_argument("--output-dir")
    parser.add_argument("--concurrency", type=int, default=3, choices=(1, 2, 3, 4))
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    summary, zip_path = asyncio.run(OptimumAdapter(args.output_dir, args.concurrency, args.delay).run())
    print(f"ZIP path = {zip_path.resolve()}")
    for key, value in summary.items():
        print(f"{key} = {value}")


if __name__ == "__main__":
    main()
