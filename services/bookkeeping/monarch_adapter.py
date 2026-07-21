"""
Monarch Money adapter — deterministic data pulls for PER.

Uses the existing Monarch GraphQL API from the monarch skill.
"""

from __future__ import annotations

import asyncio
import json
import os
import pickle
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .config import EntityConfig

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class MonarchTransaction:
    """Normalized transaction from Monarch Money."""
    id: str
    date: date
    amount: Decimal
    description: str
    merchant: Optional[str]
    category_id: Optional[str]
    category_name: Optional[str]
    account_name: Optional[str]
    needs_review: bool
    is_recurring: bool
    notes: Optional[str]


@dataclass
class MonarchAccount:
    """Account snapshot from Monarch."""
    id: str
    name: str
    balance: Decimal


@dataclass
class MonarchData:
    """All data pulled from Monarch for one period."""
    entity: str
    period_start: date
    period_end: date
    transactions: List[MonarchTransaction] = field(default_factory=list)
    accounts: List[MonarchAccount] = field(default_factory=list)
    total_income: Decimal = Decimal("0")
    total_expenses: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class MonarchAdapter:
    """
    Deterministic Monarch Money data access for PER entity.
    Uses direct aiohttp GraphQL queries (the `monarchmoney` library is broken).
    """

    def __init__(self, config: EntityConfig):
        assert config.source_type == "monarch", f"Expected monarch config, got {config.source_type}"
        self.config = config
        self.session_path = config.monarch_session_path
        self.api_url = "https://api.monarch.com/graphql"
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _load_token(self) -> str:
        """Load Monarch session token from pickle file."""
        if self._token:
            return self._token

        path = os.path.expanduser(self.session_path)
        if not os.path.exists(path):
            raise RuntimeError(
                f"Monarch session not found at {path}. "
                f"Run: python3 /home/hermes/.hermes/workspace/tools/monarch-login.py"
            )

        with open(path, "rb") as f:
            session = pickle.load(f)

        token = session.get("token")
        if not token:
            raise RuntimeError("Monarch session pickle has no 'token' field")

        self._token = token
        return token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Token {self._load_token()}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Origin": "https://app.monarchmoney.com",
            "Referer": "https://app.monarchmoney.com/",
            "Accept": "*/*",
        }

    # ------------------------------------------------------------------
    # GraphQL queries
    # ------------------------------------------------------------------

    async def _query(self, query: str, variables: Optional[Dict] = None,
                     operation_name: Optional[str] = None) -> Dict[str, Any]:
        """Execute a Monarch GraphQL query."""
        import aiohttp

        payload: Dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        if operation_name:
            payload["operationName"] = operation_name

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(
                        f"Monarch API returned HTTP {resp.status}: {text[:500]}"
                    )
                return await resp.json()

    # ------------------------------------------------------------------
    # Data pulls
    # ------------------------------------------------------------------

    async def pull_transactions(self, start: date, end: date) -> List[MonarchTransaction]:
        """
        Pull all transactions for the period.
        Uses allTransactions (NOT transactions — the singular form causes errors).
        """
        query = """
        query GetTransactions($start: String!, $end: String!) {
          allTransactions(filters: {startDate: $start, endDate: $end}) {
            totalCount
            results(offset: 0, limit: 500) {
              id
              amount
              date
              needsReview
              isRecurring
              notes
              merchant { name id }
              category { id name }
              account { id displayName }
            }
          }
        }
        """

        result = await self._query(query, variables={
            "start": start.isoformat(),
            "end": end.isoformat(),
        })

        data = result.get("data", {})
        txns_data = (
            data.get("allTransactions", {}).get("results", [])
        )

        txns: List[MonarchTransaction] = []
        for t in txns_data:
            try:
                txn_date = datetime.strptime(t["date"][:10], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                txn_date = start

            category = t.get("category") or {}
            merchant = t.get("merchant") or {}

            txns.append(MonarchTransaction(
                id=t.get("id", ""),
                date=txn_date,
                amount=Decimal(str(t.get("amount", "0"))),
                description=t.get("notes") or merchant.get("name") or "",
                merchant=merchant.get("name"),
                category_id=category.get("id"),
                category_name=category.get("name"),
                account_name=(
                    t.get("account", {}).get("displayName", "")
                    if t.get("account") else ""
                ),
                needs_review=t.get("needsReview", False),
                is_recurring=t.get("isRecurring", False),
                notes=t.get("notes"),
            ))

        return txns

    async def pull_accounts(self) -> List[MonarchAccount]:
        """Pull current account balances."""
        query = """
        query {
          accounts { id name displayName balance }
        }
        """

        result = await self._query(query)
        data = result.get("data", {})
        accounts_data = data.get("accounts", [])

        accounts: List[MonarchAccount] = []
        for a in accounts_data:
            accounts.append(MonarchAccount(
                id=a.get("id", ""),
                name=a.get("displayName") or a.get("name", ""),
                balance=Decimal(str(a.get("balance", "0"))),
            ))

        return accounts

    async def pull_all(self, start: date, end: date) -> MonarchData:
        """Pull transactions and accounts in one call."""
        txns = await self.pull_transactions(start, end)

        income = Decimal("0")
        expenses = Decimal("0")
        for t in txns:
            if t.amount > 0:
                income += t.amount
            else:
                expenses += abs(t.amount)

        accounts = await self.pull_accounts()

        return MonarchData(
            entity=self.config.entity_id,
            period_start=start,
            period_end=end,
            transactions=txns,
            accounts=accounts,
            total_income=income,
            total_expenses=expenses,
        )

    # ------------------------------------------------------------------
    # Synchronous wrapper (for pipeline)
    # ------------------------------------------------------------------

    def pull_all_sync(self, start: date, end: date) -> MonarchData:
        """Synchronous wrapper around the async pulls."""
        return asyncio.run(self.pull_all(start, end))
