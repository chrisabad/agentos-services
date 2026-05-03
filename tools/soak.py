"""Phase 0.3 sustained-rate soak.

Cycles golden-set queries at a steady rate for the configured duration,
recording per-call latency. Reports rolling p50/p99 every minute.

Usage:
  AGENTOS_MEMORY_URL=http://127.0.0.1:8010 \\
  PAPERCLIP_BOARD_KEY=... \\
  python tools/soak.py [--minutes 30] [--rps 1.0]
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from golden_set import GOLDEN_SET


def search(base_url: str, token: str, agent: str, query: str) -> int:
    qs = urllib.parse.urlencode({"agent": agent, "q": query, "limit": 10})
    req = urllib.request.Request(
        f"{base_url}/memory/search?{qs}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("AGENTOS_MEMORY_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--token", default=os.environ.get("PAPERCLIP_BOARD_KEY", ""))
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--rps", type=float, default=1.0)
    parser.add_argument(
        "--out",
        default=str(Path.home() / ".agentos" / "logs" / "memory-service-soak.csv"),
    )
    args = parser.parse_args()

    if not args.token:
        print("error: PAPERCLIP_BOARD_KEY not set", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fh = out_path.open("w", newline="")
    writer = csv.writer(fh)
    writer.writerow(["ts", "agent", "query", "status", "latency_ms"])

    deadline = time.time() + args.minutes * 60
    interval = 1.0 / args.rps if args.rps > 0 else 0.0
    samples_ms: list[float] = []
    fives: int = 0
    minute_window: list[float] = []
    next_report = time.time() + 60
    start = time.time()
    i = 0

    print(f"soaking for {args.minutes}m at ~{args.rps:.1f} rps → {out_path}")
    while time.time() < deadline:
        gq = GOLDEN_SET[i % len(GOLDEN_SET)]
        i += 1
        t0 = time.perf_counter()
        status = search(args.url, args.token, gq.agent, gq.query)
        ms = (time.perf_counter() - t0) * 1000
        samples_ms.append(ms)
        minute_window.append(ms)
        if status >= 500 or status == 0:
            fives += 1
        writer.writerow([datetime.now(timezone.utc).isoformat(), gq.agent, gq.query, status, f"{ms:.1f}"])
        fh.flush()

        if time.time() >= next_report:
            elapsed_min = (time.time() - start) / 60
            p50 = percentile(minute_window, 50)
            p99 = percentile(minute_window, 99)
            print(f"  +{elapsed_min:5.1f}m  n={len(minute_window):4d}  p50={p50:6.1f}ms  p99={p99:6.1f}ms  5xx={fives}")
            minute_window = []
            next_report += 60

        sleep_for = interval - (time.perf_counter() - t0)
        if sleep_for > 0:
            time.sleep(sleep_for)

    fh.close()

    print()
    print(f"=== soak summary (n={len(samples_ms)}) ===")
    print(f"  duration   {(time.time() - start) / 60:.1f} min")
    print(f"  total      {len(samples_ms)} calls")
    print(f"  5xx        {fives}")
    print(f"  p50        {percentile(samples_ms, 50):.1f} ms")
    print(f"  p95        {percentile(samples_ms, 95):.1f} ms")
    print(f"  p99        {percentile(samples_ms, 99):.1f} ms")
    print(f"  max        {max(samples_ms):.1f} ms")
    print(f"  avg        {statistics.mean(samples_ms):.1f} ms")
    print(f"  CSV        {out_path}")

    return 0 if fives == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
