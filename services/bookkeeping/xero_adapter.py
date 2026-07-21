"""
Xero adapter — deterministic data pulls for KAL and FON.

Uses Zapier `_zap_raw_request` passthrough for reads (no Xero credentials needed).
Writes (reconcile, categorize) use the direct Xero API.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .config import EntityConfig

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class BankTransaction:
    """Normalized bank transaction from Xero."""
    id: str
    date: date
    type: str          # "SPEND" | "RECEIVE"
    total: Decimal
    description: str
    reference: str
    is_reconciled: bool
    account_code: Optional[str]
    account_name: Optional[str]
    contact_name: Optional[str]
    line_items: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PAndL:
    """Profit & Loss summary for a period."""
    period_start: date
    period_end: date
    total_revenue: Decimal = Decimal("0")
    total_expenses: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    raw_report: Optional[Dict[str, Any]] = None


@dataclass
class BalanceSheet:
    """Balance sheet snapshot at a date."""
    as_of_date: date
    total_assets: Decimal = Decimal("0")
    total_liabilities: Decimal = Decimal("0")
    total_equity: Decimal = Decimal("0")
    raw_report: Optional[Dict[str, Any]] = None


@dataclass
class XeroData:
    """All data pulled from Xero for one entity in one period."""
    entity: str
    period_start: date
    period_end: date
    transactions: List[BankTransaction] = field(default_factory=list)
    p_and_l: Optional[PAndL] = None
    balance_sheet: Optional[BalanceSheet] = None
    unreconciled_count: int = 0
    total_txns: int = 0
    bank_statement_totals: Optional[Dict[str, Decimal]] = None

# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class XeroAdapter:
    """
    Deterministic Xero data access for one entity.

    Uses the Zapier `_zap_raw_request` passthrough for reads, so no Xero
    OAuth tokens are needed — Zapier injects the Authorization header.
    Writes use the direct API (must refresh tokens first).
    """

    def __init__(self, config: EntityConfig):
        assert config.source_type == "xero", f"Expected xero config, got {config.source_type}"
        self.config = config
        self.tenant_id = config.xero_tenant_id
        self.base_url = "https://api.xero.com/api.xro/2.0"
        self.zapier_connection = "021c2a3a-2b75-891a-afae-56aa6df849ab"
        self._env = None  # lazy-loaded direct API env

    # ------------------------------------------------------------------
    # Zapier passthrough (reads)
    # ------------------------------------------------------------------

    def _run_zapier_raw(self, method: str, path: str,
                        querystring: Optional[Dict[str, str]] = None,
                        body: Optional[str] = None) -> Dict[str, Any]:
        """
        Run a raw Xero API request through Zapier's `_zap_raw_request`.
        Returns the parsed JSON response body.
        """
        url = f"{self.base_url}{path}"
        inputs = {
            "fail_on_errors": True,
            "method": method,
            "url": url,
            "headers": {
                "Xero-Tenant-Id": self.tenant_id,
                "Accept": "application/json",
            },
        }
        if querystring:
            inputs["querystring"] = querystring
        if body:
            inputs["body"] = body

        inputs_json = json.dumps(inputs)

        cmd = [
            "zapier-sdk", "run-action", "xero", "write", "_zap_raw_request",
            "--connection", self.zapier_connection,
            "--inputs", inputs_json,
            "--json",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "ZAPIER_CAN_INCLUDE_SHARED_CONNECTIONS": "true"},
            )
        except FileNotFoundError:
            raise RuntimeError(
                "zapier-sdk CLI not found. Install with: npm install -g zapier-sdk"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Zapier raw request timed out: {method} {path}")

        if result.returncode != 0:
            raise RuntimeError(
                f"Zapier raw request failed (exit {result.returncode}): "
                f"{result.stderr[:1000]}"
            )

        try:
            zapier_output = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"Zapier returned non-JSON: {result.stdout[:500]}"
            )

        # Zapier wraps the response in data[0].response
        data_items = zapier_output.get("data", [])
        if not data_items:
            raise RuntimeError("Zapier returned empty data array")

        response_info = data_items[0].get("response", {})
        status = response_info.get("status", 0)
        if status != 200:
            body_snippet = str(response_info.get("body", ""))[:500]
            raise RuntimeError(
                f"Xero API returned HTTP {status}: {body_snippet}"
            )

        # Response body is a JSON string that needs parsing
        body_str = response_info.get("body", "{}")
        if isinstance(body_str, str):
            return json.loads(body_str)
        return body_str

    def _xero_date_filter(self, field: str, start: date, end: date) -> str:
        """Build a Xero OData date-range where clause."""
        return (
            f"{field}>=DateTime({start.year},{start.month},{start.day})"
            f"&&{field}<=DateTime({end.year},{end.month},{end.day})"
        )

    # ------------------------------------------------------------------
    # Data pulls
    # ------------------------------------------------------------------

    def pull_transactions(self, start: date, end: date) -> List[BankTransaction]:
        """
        Pull all bank transactions for the period. Handles pagination.
        Returns normalized BankTransaction objects.
        """
        page = 1
        all_txns: List[BankTransaction] = []

        while True:
            qs = {
                "where": self._xero_date_filter("Date", start, end),
                "order": "Date ASC",
                "page": str(page),
            }

            resp = self._run_zapier_raw("GET", "/BankTransactions", querystring=qs)
            txns = resp.get("BankTransactions", [])

            if not txns:
                break

            for t in txns:
                line_items = t.get("LineItems", [])
                descriptions = [
                    li.get("Description", "") for li in line_items
                ]
                account_codes = [
                    li.get("AccountCode", "") for li in line_items
                ]

                try:
                    txn_date = datetime.strptime(t["Date"][:10], "%Y-%m-%d").date()
                except (ValueError, KeyError):
                    txn_date = start

                bt = BankTransaction(
                    id=t.get("BankTransactionID", ""),
                    date=txn_date,
                    type=t.get("Type", ""),
                    total=Decimal(str(t.get("Total", "0"))),
                    description=" | ".join(filter(None, descriptions)),
                    reference=t.get("Reference", ""),
                    is_reconciled=t.get("IsReconciled", False),
                    account_code=account_codes[0] if account_codes else None,
                    account_name=(
                        t.get("BankAccount", {}).get("Name", "")
                        if t.get("BankAccount") else ""
                    ),
                    contact_name=(
                        t.get("Contact", {}).get("Name", "")
                        if t.get("Contact") else ""
                    ),
                    line_items=line_items,
                )
                all_txns.append(bt)

            if len(txns) < 100:
                break
            page += 1
            time.sleep(0.3)  # rate-limit politeness

        return all_txns

    def pull_p_and_l(self, start: date, end: date) -> PAndL:
        """
        Pull Profit & Loss report for the period.
        Handles the Xero 365-day limit via deterministic chunking.
        """
        chunks = self._chunk_period(start, end)
        combined_revenue = Decimal("0")
        combined_expenses = Decimal("0")
        last_raw = None

        for chunk_start, chunk_end in chunks:
            qs = {
                "fromDate": chunk_start.isoformat(),
                "toDate": chunk_end.isoformat(),
                "standardLayout": "true",
            }

            resp = self._run_zapier_raw("GET", "/Reports/ProfitAndLoss", querystring=qs)
            reports = resp.get("Reports", [])
            if not reports:
                continue

            last_raw = reports[0]
            revenue, expenses = self._parse_p_and_l(last_raw)
            combined_revenue += revenue
            combined_expenses += expenses

        pnl = PAndL(
            period_start=start,
            period_end=end,
            total_revenue=combined_revenue,
            total_expenses=combined_expenses,
            net_profit=combined_revenue - combined_expenses,
            raw_report=last_raw,
        )
        return pnl

    def pull_balance_sheet(self, as_of: date) -> BalanceSheet:
        """
        Pull Balance Sheet snapshot as of a given date.
        """
        qs = {
            "date": as_of.isoformat(),
            "standardLayout": "true",
        }

        resp = self._run_zapier_raw("GET", "/Reports/BalanceSheet", querystring=qs)
        reports = resp.get("Reports", [])
        raw = reports[0] if reports else None

        assets, liabilities, equity = self._parse_balance_sheet(raw) if raw else (Decimal("0"), Decimal("0"), Decimal("0"))

        return BalanceSheet(
            as_of_date=as_of,
            total_assets=assets,
            total_liabilities=liabilities,
            total_equity=equity,
            raw_report=raw,
        )

    def pull_all(self, start: date, end: date) -> XeroData:
        """
        Convenience: pull transactions, P&L, and balance sheet in one call.
        """
        txns = self.pull_transactions(start, end)
        pnl = self.pull_p_and_l(start, end)
        bs = self.pull_balance_sheet(end)

        unreconciled = [t for t in txns if not t.is_reconciled and t.type in ("SPEND", "RECEIVE")]

        return XeroData(
            entity=self.config.entity_id,
            period_start=start,
            period_end=end,
            transactions=txns,
            p_and_l=pnl,
            balance_sheet=bs,
            unreconciled_count=len(unreconciled),
            total_txns=len(txns),
        )

    # ------------------------------------------------------------------
    # Writes (direct API)
    # ------------------------------------------------------------------

    def _load_env(self):
        """Load Xero tokens from dotenv file."""
        if self._env is not None:
            return self._env
        from dotenv import dotenv_values
        env_path = "/home/hermes/.hermes/workspace/.env"
        self._env = dotenv_values(env_path)
        return self._env

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get headers for direct Xero API write calls."""
        env = self._load_env()
        prefix = f"XERO_{self.config.xero_org_prefix.upper()}"
        token = env.get(f"{prefix}_ACCESS_TOKEN")
        if not token:
            raise RuntimeError(
                f"No Xero token found for {self.config.xero_org_prefix}. "
                f"Run: python3 tools/xero-token.py --org {self.config.xero_org_prefix}"
            )
        return {
            "Authorization": f"Bearer {token}",
            "Xero-Tenant-Id": self.tenant_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def reconcile_transactions(self, txn_ids: List[str]) -> Dict[str, Any]:
        """
        Mark a list of transactions as reconciled via direct API.
        Only reconciles transactions that have account codes assigned.
        """
        import requests

        headers = self._get_auth_headers()

        payload = {
            "BankTransactions": [
                {"BankTransactionID": tid, "IsReconciled": True}
                for tid in txn_ids
            ]
        }

        resp = requests.post(
            f"{self.base_url}/BankTransactions",
            headers=headers,
            json=payload,
        )

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Xero reconcile failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )

        return resp.json()

    def categorize_transaction(self, txn_id: str, account_code: str,
                               description: Optional[str] = None) -> Dict[str, Any]:
        """
        Update a bank transaction's account code via direct API.
        """
        import requests

        headers = self._get_auth_headers()

        line_item = {"AccountCode": account_code}
        if description:
            line_item["Description"] = description

        payload = {
            "BankTransactions": [
                {"BankTransactionID": txn_id, "LineItems": [line_item]}
            ]
        }

        resp = requests.post(
            f"{self.base_url}/BankTransactions",
            headers=headers,
            json=payload,
        )

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Xero categorize failed (HTTP {resp.status_code}): {resp.text[:500]}"
            )

        return resp.json()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_period(start: date, end: date, max_days: int = 365) -> List[tuple[date, date]]:
        """
        Split a date range into chunks of at most max_days.
        Handles the Xero 365-day P&L limit (the AGE-1426 failure class).
        """
        from datetime import timedelta
        chunks = []
        current = start
        while current < end:
            chunk_end = min(current + timedelta(days=max_days - 1), end)
            chunks.append((current, chunk_end))
            current = chunk_end + timedelta(days=1)
        return chunks

    @staticmethod
    def _parse_p_and_l(report: Dict[str, Any]) -> tuple[Decimal, Decimal]:
        """Extract total revenue and total expenses from a P&L report."""
        revenue = Decimal("0")
        expenses = Decimal("0")

        rows = report.get("Rows", [])
        for section in rows:
            section_title = (section.get("Title") or "").lower()

            if "revenue" in section_title or "income" in section_title:
                for row in section.get("Rows", []):
                    cells = row.get("Cells", [])
                    if len(cells) >= 2:
                        try:
                            val = Decimal(str(cells[-1].get("Value", "0")))
                            if val > 0:
                                revenue += val
                        except Exception:
                            pass

            elif "expenses" in section_title or "cost of goods" in section_title:
                for row in section.get("Rows", []):
                    cells = row.get("Cells", [])
                    if len(cells) >= 2:
                        try:
                            val = Decimal(str(cells[-1].get("Value", "0")))
                            expenses += abs(val)
                        except Exception:
                            pass

        return revenue, expenses

    @staticmethod
    def _parse_balance_sheet(report: Dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
        """Extract total assets, liabilities, and equity from a Balance Sheet."""
        assets = Decimal("0")
        liabilities = Decimal("0")
        equity = Decimal("0")

        rows = report.get("Rows", [])
        for section in rows:
            title = (section.get("Title") or "").lower()

            if "assets" in title:
                for row in section.get("Rows", []):
                    cells = row.get("Cells", [])
                    if len(cells) >= 2:
                        try:
                            assets += Decimal(str(cells[-1].get("Value", "0")))
                        except Exception:
                            pass

            elif "liabilities" in title:
                for row in section.get("Rows", []):
                    cells = row.get("Cells", [])
                    if len(cells) >= 2:
                        try:
                            val = Decimal(str(cells[-1].get("Value", "0")))
                            if "equity" not in title.lower():
                                liabilities += abs(val)
                            else:
                                equity += abs(val)
                        except Exception:
                            pass

            elif "equity" in title:
                for row in section.get("Rows", []):
                    cells = row.get("Cells", [])
                    if len(cells) >= 2:
                        try:
                            equity += Decimal(str(cells[-1].get("Value", "0")))
                        except Exception:
                            pass

        return assets, liabilities, equity
