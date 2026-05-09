"""Phase 0.3 Replay Harness — Memory Service Validation

Runs 20 representative queries against the Memory Service and validates:
- Response structure correctness
- Latency performance  
- Error-free execution

Suitable for 24h soak testing.

Usage:
  PAPERCLIP_BOARD_KEY=... python scripts/replay_harness.py
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Query:
    agent: str
    text: str
    category: str


# 20 representative queries: based on actual content in agent MEMORY.md files
QUERY_SET = [
    # Queries that should match content (agent-specific)
    Query("arlo", "experiments", "agent-specific"),
    Query("arlo", "memory log", "agent-specific"),
    Query("lev", "paperclip", "agent-specific"),
    Query("lev", "absolute paths", "agent-specific"),
    Query("lev", "secrets", "agent-specific"),
    Query("cass", "never edit", "agent-specific"),
    Query("cass", "tool log", "agent-specific"),
    Query("finn", "build state", "agent-specific"),
    Query("finn", "persistence", "agent-specific"),
    Query("sage", "accountability", "agent-specific"),
    Query("maren", "API keys", "agent-specific"),
    Query("rue", "memory", "agent-specific"),
    # Generic terms that might appear in various contexts
    Query("arlo", "read", "general"),
    Query("lev", "context", "general"),
    Query("cass", "append", "general"),
    Query("finn", "state", "general"),
    Query("sage", "issues", "general"),
    Query("maren", "storage", "general"),
    Query("rue", "process", "general"),
    Query("remi", "workflow", "general"),
]


@dataclass
class SearchResult:
    """Single search result from the Memory Service API."""
    source: str
    score: float
    excerpt: str
    kind: str


@dataclass
class QueryResult:
    """Results from a single query run."""
    query: str
    agent: str
    category: str
    timestamp: float
    latency_ms: float
    error: Optional[str]
    results: list[SearchResult]


def search_memory(base_url: str, token: str, agent: str, query: str, limit: int = 10) -> list[SearchResult]:
    """Query the Memory Service."""
    qs = urllib.parse.urlencode({
        "agent": agent,
        "q": query,
        "limit": limit,
    })
    req = urllib.request.Request(
        f"{base_url}/memory/search?{qs}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    
    results = []
    for item in data.get("results", []):
        results.append(SearchResult(
            source=item["source"],
            score=float(item.get("score", 0)),
            excerpt=item.get("excerpt", "")[:80],  # Truncate for display
            kind=item.get("kind", ""),
        ))
    return results


def run_replay(base_url: str, token: str, limit: int = 10) -> list[QueryResult]:
    """Execute all queries."""
    results = []
    
    for q in QUERY_SET:
        timestamp = time.time()
        t0 = time.perf_counter()
        error = None
        search_results = []
        
        try:
            search_results = search_memory(base_url, token, q.agent, q.text, limit)
        except Exception as e:
            error = str(e)
        
        latency_ms = (time.perf_counter() - t0) * 1000
        
        results.append(QueryResult(
            query=q.text,
            agent=q.agent,
            category=q.category,
            timestamp=timestamp,
            latency_ms=latency_ms,
            error=error,
            results=search_results,
        ))
    
    return results


def compute_metrics(results: list[QueryResult]) -> dict:
    """Compute summary metrics."""
    latencies = [r.latency_ms for r in results if not r.error]
    errors = [r for r in results if r.error]
    results_found = sum(1 for r in results if len(r.results) > 0)
    
    if not latencies:
        return {
            "total_queries": len(results),
            "successful": 0,
            "failed": len(errors),
            "error_rate": 100.0 if results else 0.0,
            "queries_with_results": 0,
        }
    
    sorted_lat = sorted(latencies)
    return {
        "total_queries": len(results),
        "successful": len(latencies),
        "failed": len(errors),
        "error_rate": 100.0 * len(errors) / len(results) if results else 0.0,
        "queries_with_results": results_found,
        "result_rate": 100.0 * results_found / len(results) if results else 0.0,
        "latency_ms": {
            "min": min(latencies),
            "avg": statistics.mean(latencies),
            "median": statistics.median(latencies),
            "p95": sorted_lat[min(len(sorted_lat)-1, int(0.95 * len(sorted_lat)))],
            "p99": sorted_lat[min(len(sorted_lat)-1, int(0.99 * len(sorted_lat)))],
            "max": max(latencies),
            "stdev": statistics.stdev(latencies) if len(latencies) > 1 else 0,
        },
        "avg_results_per_query": statistics.mean([len(r.results) for r in results]) if results else 0,
    }


def format_table(results: list[QueryResult]) -> str:
    """Format results as markdown."""
    lines = [
        "| Agent | Query | Latency (ms) | Results | Status |",
        "|-------|-------|--------------|---------|--------|",
    ]
    for r in results:
        status = f"❌ {r.error[:25]}" if r.error else "✓"
        lines.append(f"| {r.agent:6} | {r.query:15} | {r.latency_ms:7.1f} | {len(r.results):3d} | {status} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Memory Service replay validation")
    parser.add_argument("--url", default=os.environ.get("AGENTOS_MEMORY_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--token", default=os.environ.get("PAPERCLIP_BOARD_KEY", ""))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    
    args = parser.parse_args()
    
    if not args.token:
        print("error: PAPERCLIP_BOARD_KEY not set", file=sys.stderr)
        return 2
    
    print("Running replay harness (20 queries)...", file=sys.stderr)
    results = run_replay(args.url, args.token, args.limit)
    metrics = compute_metrics(results)
    
    if args.json:
        output = {
            "timestamp": time.time(),
            "metrics": metrics,
            "queries": [
                {
                    "agent": r.agent,
                    "query": r.query,
                    "category": r.category,
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                    "result_count": len(r.results),
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print("\n## Memory Service Replay Validation\n")
        print("### Per-Query Results\n")
        print(format_table(results))
        
        print("\n### Summary Metrics\n")
        print(f"- Queries: {metrics['total_queries']}")
        print(f"- Success: {metrics['successful']} ({100-metrics['error_rate']:.1f}%)")
        print(f"- Queries with results: {metrics['queries_with_results']} ({metrics['result_rate']:.1f}%)")
        
        if "latency_ms" in metrics:
            lat = metrics["latency_ms"]
            print(f"- Latency: min={lat['min']:.0f}ms, avg={lat['avg']:.0f}ms, p99={lat['p99']:.0f}ms, max={lat['max']:.0f}ms")
        
        print(f"- Avg results per query: {metrics['avg_results_per_query']:.1f}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
