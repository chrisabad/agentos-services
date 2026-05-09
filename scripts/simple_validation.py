#!/usr/bin/env python3
"""Phase 0.3 Simple Validation — Test Memory Service responses without board key auth"""

import json
import sys
import urllib.request
from urllib.error import HTTPError

BASE_URL = "http://127.0.0.1:8010"
GATEWAY_BASE = "http://127.0.0.1:18790"

# Test queries (subset from replay harness)
QUERIES = [
    ("axel", "gateway"),
    ("axel", "memory"),
    ("arlo", "experiments"),
    ("lev", "paperclip"),
]

def test_auth():
    """Test authentication requirement"""
    print("Testing authentication...", file=sys.stderr)
    try:
        req = urllib.request.Request(f"{BASE_URL}/health")
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read().decode())
            print(f"✓ Health endpoint (no auth): {data['status']}", file=sys.stderr)
    except Exception as e:
        print(f"✗ Health endpoint failed: {e}", file=sys.stderr)
        return False
    
    # Try with dummy token
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/memory/search?agent=test&q=test&limit=1",
            headers={"Authorization": "Bearer dummy"}
        )
        with urllib.request.urlopen(req) as r:
            print(f"✓ Search accepted dummy token (unexpected!)", file=sys.stderr)
            return True
    except HTTPError as e:
        if e.code == 401:
            print(f"✓ Search correctly rejects unauthorized (401)", file=sys.stderr)
            return False
        raise

def main():
    print("Phase 0.3 Validation: Memory Service Response Structure", file=sys.stderr)
    print(f"Target: {BASE_URL}", file=sys.stderr)
    
    has_token = test_auth()
    
    if not has_token:
        print(f"\nBlocker: PAPERCLIP_BOARD_KEY not available for authentication.", file=sys.stderr)
        print(f"Cannot run full validation harness without board key.", file=sys.stderr)
        print(f"\nWorkarounds:", file=sys.stderr)
        print(f"1. Locate/generate PAPERCLIP_BOARD_KEY from Paperclip", file=sys.stderr)
        print(f"2. Temporarily set PAPERCLIP_BOARD_KEY in Memory Service env", file=sys.stderr)
        print(f"3. Use gateway memory_search tool for comparison instead", file=sys.stderr)
        
        return 1
    
    print(f"\nRunning {len(QUERIES)} test queries...")
    results = []
    for agent, query in QUERIES:
        try:
            url = f"{BASE_URL}/memory/search?agent={agent}&q={query}&limit=5"
            # Would need valid token here
            print(f"  {agent}/{query}: <would test with valid token>")
        except Exception as e:
            print(f"  {agent}/{query}: ERROR - {e}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
