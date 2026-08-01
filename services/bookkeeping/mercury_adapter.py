"""
Mercury adapter — pulls bank transaction data for feed-gap checking.

Fetches settled transactions from a Mercury checking account via the
Mercury API, so INV10 can cross-check every Mercury-settled transaction
against Xero's BankTransactions.

The Mercury API token is stored in AWS Secrets Manager at
`agentos/fon/mercury_api_token` and must be available at runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class MercuryData:
    """All Mercury data for one account in one period."""
    account_id: str
    account_name: str
    period_start: date
    period_end: date
    transactions: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def settled_count(self) -> int:
        return sum(1 for t in self.transactions if t.get("status") == "sent")

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self.transactions if t.get("status") != "sent")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

MERCURY_API_URL = "https://api.mercury.com/api/v1"


class MercuryAdapter:
    """
    Pulls bank transactions from the Mercury API.

    Requires a Mercury API token. The token is read from AWS Secrets Manager
    by sourcing a script that exports it, or from the MERCURY_API_TOKEN
    environment variable directly.
    """

    def __init__(
        self,
        account_id: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        """
        Args:
            account_id: Font Replacer Mercury account ID. If None, fetched
                        from the API on first call.
            api_token: Mercury API token. If None, read from the env var
                       or fetched from AWS Secrets Manager.
        """
        self.account_id = account_id
        self.api_token = api_token or self._resolve_token()

        if not self.api_token:
            raise RuntimeError(
                "Mercury API token not available. "
                "Set MERCURY_API_TOKEN env var or ensure the AWS SM secret "
                "`agentos/fon/mercury_api_token` is accessible."
            )

    # ------------------------------------------------------------------
    # Token resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_token() -> str:
        """Resolve the Mercury API token from env or AWS Secrets Manager."""
        token = os.environ.get("MERCURY_API_TOKEN", "")
        if token:
            return token

        # Try fetching from AWS SM
        try:
            result = subprocess.run(
                [
                    "aws", "secretsmanager", "get-secret-value",
                    "--secret-id", "agentos/fon/mercury_api_token",
                    "--query", "SecretString",
                    "--output", "text",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return ""

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def _request(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Make a GET request to the Mercury API and return parsed JSON."""
        url = f"{MERCURY_API_URL}{path}"

        cmd = [
            "curl", "-sg",
            "-H", f"Authorization: Bearer {self.api_token}",
            "-H", "Accept: application/json",
            url,
        ]

        if params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            cmd[-1] = f"{url}?{query_string}"

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Mercury API curl failed (exit {result.returncode}): "
                f"{result.stderr[:500]}"
            )

        if not result.stdout.strip():
            raise RuntimeError("Mercury API returned empty response")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"Mercury API returned non-JSON: {result.stdout[:500]}"
            )

    def _paginate(self, path: str, params: Optional[Dict[str, str]] = None,
                  limit: int = 500) -> List[Dict[str, Any]]:
        """Fetch all pages of a list endpoint using cursor-based pagination."""
        all_items: List[Dict[str, Any]] = []
        query_params = dict(params or {})
        query_params["limit"] = str(limit)
        cursor: Optional[str] = None

        while True:
            if cursor:
                query_params["startingAfter"] = cursor

            data = self._request(path, query_params)

            items = data.get("transactions", [])
            if not items:
                break

            all_items.extend(items)

            # Mercury uses cursor-based pagination
            cursor = data.get("totalNumberOfTransactions")
            # totalNumberOfTransactions is the count, not a cursor.
            # Mercury returns all items that match — if fewer than limit,
            # we're done. Otherwise, use the last item's ID as cursor.
            if len(items) < limit:
                break
            cursor = items[-1].get("id")

        return all_items

    # ------------------------------------------------------------------
    # Account resolution
    # ------------------------------------------------------------------

    def _resolve_account_id(self) -> str:
        """Fetch the first checking account ID from Mercury."""
        if self.account_id:
            return self.account_id

        data = self._request("/accounts")
        accounts = data.get("accounts", [])
        # Pick the first checking-type account
        for acct in accounts:
            acct_type = acct.get("type", "").lower()
            if acct_type in ("checking", "mercury"):
                self.account_id = acct.get("id", "")
                if self.account_id:
                    return self.account_id

        # Fallback: just use the first account
        if accounts:
            self.account_id = accounts[0].get("id", "")
            return self.account_id

        raise RuntimeError("No Mercury accounts found")

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def pull_transactions(
        self,
        start: date,
        end: date,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all transactions in the given date range.

        Args:
            start: Start date (inclusive)
            end: End date (inclusive)
            status: Optional filter — "sent" for settled, "pending" for pending

        Returns:
            List of raw Mercury transaction dicts with keys:
                id, amount, status, counterpartyName, bankDescription,
                postedAt, createdAt
        """
        account_id = self._resolve_account_id()
        params: Dict[str, str] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        if status:
            params["status"] = status

        path = f"/account/{account_id}/transactions"
        return self._paginate(path, params)

    def pull_all(self, start: date, end: date) -> MercuryData:
        """Pull all Mercury data for the period."""
        account_id = self._resolve_account_id()

        # Get account name
        try:
            acct_data = self._request(f"/account/{account_id}")
            account_name = acct_data.get("name", "Mercury Checking")
        except (RuntimeError, KeyError):
            account_name = "Mercury Checking"

        txns = self.pull_transactions(start, end)

        return MercuryData(
            account_id=account_id,
            account_name=account_name,
            period_start=start,
            period_end=end,
            transactions=txns,
        )
