"""Phase 1.2 broker quality eval against the golden set.

Usage:
  AGENTOS_BROKER_URL=http://127.0.0.1:8011 \\
  PAPERCLIP_BOARD_KEY=... \\
  python tools/eval_broker.py

Pass if expected-decision agreement ≥ 80%.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from broker_golden_set import GOLDEN_SET, GoldenCase


def check(base_url: str, token: str, request: dict) -> dict:
    req = urllib.request.Request(
        f"{base_url}/broker/check",
        data=json.dumps(request).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def evaluate(case: GoldenCase, response: dict) -> tuple[bool, str]:
    if response.get("decision") != case.expected_decision:
        return False, f"decision: got {response.get('decision')}, expected {case.expected_decision}"
    if case.expected_rule is not None and response.get("rule_id") != case.expected_rule:
        return False, f"rule: got {response.get('rule_id')}, expected {case.expected_rule}"
    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("AGENTOS_BROKER_URL", "http://127.0.0.1:8011"))
    parser.add_argument("--token", default=os.environ.get("PAPERCLIP_BOARD_KEY", ""))
    parser.add_argument("--dry-run", action="store_true", help="ask broker to evaluate without persisting")
    args = parser.parse_args()
    if not args.token:
        print("error: PAPERCLIP_BOARD_KEY not set", file=sys.stderr)
        return 2

    rows: list[tuple[GoldenCase, bool, str, dict]] = []
    for case in GOLDEN_SET:
        req = dict(case.request)
        if args.dry_run:
            req["dry_run"] = True
        try:
            resp = check(args.url, args.token, req)
        except urllib.error.HTTPError as e:
            body = e.read()[:200] if hasattr(e, "read") else b""
            rows.append((case, False, f"http {e.code}: {body!r}", {}))
            continue
        except Exception as e:
            rows.append((case, False, f"error: {e}", {}))
            continue
        ok, detail = evaluate(case, resp)
        rows.append((case, ok, detail, resp))

    passed = sum(1 for _, ok, _, _ in rows if ok)
    total = len(rows)

    print()
    print(f"{'#':<3} {'desc':<55} {'pass':<5} {'rule':<8} {'detail':<40}")
    print("-" * 115)
    for i, (case, ok, detail, resp) in enumerate(rows, start=1):
        flag = "✓" if ok else "✗"
        rid = resp.get("rule_id", "-")
        print(f"{i:<3} {case.description[:54]:<55} {flag:<5} {rid:<8} {detail[:39]:<40}")
    print("-" * 115)
    print(f"agreement: {passed}/{total} = {passed/total:.0%}  (target ≥ 80%)")

    return 0 if passed / total >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
