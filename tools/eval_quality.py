"""Phase 0.3 quality evaluation against the golden set.

Usage:
  AGENTOS_MEMORY_URL=http://127.0.0.1:8010 \\
  PAPERCLIP_BOARD_KEY=... \\
  python tools/eval_quality.py

Pass if top-3 hit rate ≥ 80%.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from golden_set import GOLDEN_SET, GoldenQuery


def search(base_url: str, token: str, agent: str, query: str, limit: int = 10) -> dict:
    qs = urllib.parse.urlencode({"agent": agent, "q": query, "limit": limit})
    req = urllib.request.Request(
        f"{base_url}/memory/search?{qs}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def score_query(top_results: list[dict], expected: str) -> tuple[bool, int | None]:
    """Return (hit_in_top3, hit_rank). hit_rank is 1-indexed across all returned results."""
    needle = expected.lower()
    for idx, r in enumerate(top_results):
        if needle in r.get("excerpt", "").lower():
            return idx < 3, idx + 1
    return False, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("AGENTOS_MEMORY_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--token", default=os.environ.get("PAPERCLIP_BOARD_KEY", ""))
    args = parser.parse_args()
    if not args.token:
        print("error: PAPERCLIP_BOARD_KEY not set", file=sys.stderr)
        return 2

    hits = 0
    misses = 0
    rows: list[tuple[GoldenQuery, bool, int | None, float]] = []

    for q in GOLDEN_SET:
        t0 = time.perf_counter()
        try:
            data = search(args.url, args.token, q.agent, q.query)
        except Exception as e:
            print(f"  FAIL  {q.agent} '{q.query}' — {e}")
            misses += 1
            rows.append((q, False, None, 0.0))
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000
        hit, rank = score_query(data.get("results", []), q.expected_substring)
        rows.append((q, hit, rank, elapsed_ms))
        if hit:
            hits += 1
        else:
            misses += 1

    total = len(GOLDEN_SET)
    rate = hits / total if total else 0.0

    print()
    print(f"{'agent':<12} {'query':<35} {'hit':<5} {'rank':<5} {'ms':>7}")
    print("-" * 70)
    for q, hit, rank, ms in rows:
        flag = "✓" if hit else "✗"
        rk = str(rank) if rank else "-"
        print(f"{q.agent:<12} {q.query[:34]:<35} {flag:<5} {rk:<5} {ms:>7.1f}")
    print("-" * 70)
    print(f"hit rate: {hits}/{total} = {rate:.0%}  (target ≥ 80%)")

    return 0 if rate >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
