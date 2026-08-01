"""Metrics CLI — deterministic revenue/MRR answers with labeled coverage.

    python -m services.metrics revenue --business fon --period ytd [--json]
    python -m services.metrics mrr --business fon [--json]

Output contract (the point of this tool): every answer states PERIOD,
BASIS, and per-rail coverage, including rails that could NOT be reached —
so an agent can quote it without accidentally presenting a partial or
lifetime figure as something else. See AGE-2787 for the failure class.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import List, Optional

from services.bookkeeping.config import load_config
from services.bookkeeping.lemonsqueezy_adapter import LemonSqueezyAdapter
from services.metrics.periods import Period, parse_period
from services.metrics.registry import Business, Rail, get_business


@dataclass
class RailResult:
    rail: str
    label: str
    basis: str
    available: bool
    amount_cents: Optional[int] = None
    detail: dict = field(default_factory=dict)
    warning: str = ""


@dataclass
class RevenueAnswer:
    business: str
    period_label: str
    rails: List[RailResult] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return sum(r.amount_cents or 0 for r in self.rails if r.available)

    @property
    def mixed_basis(self) -> bool:
        bases = {r.basis for r in self.rails if r.available and r.amount_cents}
        return len(bases) > 1

    def to_text(self) -> str:
        lines = [
            f"BUSINESS: {self.business}",
            f"PERIOD:   {self.period_label}",
        ]
        for r in self.rails:
            if r.available:
                lines.append(
                    f"RAIL:     {r.label} [{r.basis}] = ${(r.amount_cents or 0) / 100:,.2f}"
                )
                for k, v in r.detail.items():
                    lines.append(f"            {k}: {v}")
            else:
                lines.append(f"RAIL:     {r.label} — UNAVAILABLE ({r.warning})")
        covered = [r for r in self.rails if r.available]
        missing = [r for r in self.rails if not r.available]
        basis_note = (
            "MIXED BASIS — gross and net-payout rails summed; do not compare "
            "this total directly to a single ledger or platform figure"
            if self.mixed_basis
            else (covered[0].basis if covered else "n/a")
        )
        lines.append(f"TOTAL:    ${self.total_cents / 100:,.2f}  [basis: {basis_note}]")
        if missing:
            lines.append(
                "COVERAGE: PARTIAL — missing rails: "
                + ", ".join(r.rail for r in missing)
                + ". This total UNDERSTATES the business."
            )
        else:
            lines.append("COVERAGE: all registered rails included")
        return "\n".join(lines)


def _ls_revenue_rail(biz: Business, rail: Rail, period: Period) -> RailResult:
    try:
        if not biz.ls_store_id:
            raise ValueError(f"{biz.name} has no Lemon Squeezy store registered")
        adapter = LemonSqueezyAdapter(store_id=biz.ls_store_id, product_id=biz.ls_product_id)
        rev = adapter.compute_revenue(period.start, period.end)
        return RailResult(
            rail=rail.key,
            label=rail.label,
            basis=rail.basis,
            available=True,
            amount_cents=rev.gross_cents,
            detail={
                "orders": f"{rev.order_count} paid = ${rev.order_cents / 100:,.2f}",
                "renewals": f"{rev.renewal_count} paid = ${rev.renewal_cents / 100:,.2f}",
                "refunded": f"{rev.refunded_order_count} orders (${rev.refunded_order_cents / 100:,.2f})",
            },
        )
    except Exception as e:  # missing key, API failure — degrade with a label
        return RailResult(
            rail=rail.key, label=rail.label, basis=rail.basis,
            available=False, warning=str(e)[:160],
        )


def _figma_payout_rail(biz: Business, rail: Rail, period: Period) -> RailResult:
    try:
        from services.bookkeeping.xero_adapter import XeroAdapter

        cfg = load_config(biz.entity_id)
        adapter = XeroAdapter(cfg)
        txns = adapter.pull_transactions(period.start, period.end)
        figma = [
            t for t in txns
            if t.type.startswith("RECEIVE")
            and "figma" in (t.contact_name or "").lower()
        ]
        cents = round(sum(t.total for t in figma) * 100)
        return RailResult(
            rail=rail.key, label=rail.label, basis=rail.basis,
            available=True, amount_cents=cents,
            detail={"deposits": str(len(figma)), "caveat": rail.notes},
        )
    except Exception as e:
        return RailResult(
            rail=rail.key, label=rail.label, basis=rail.basis,
            available=False, warning=str(e)[:160],
        )


def _xero_pnl_rail(biz: Business, rail: Rail, period: Period) -> RailResult:
    try:
        from services.bookkeeping.xero_adapter import XeroAdapter

        cfg = load_config(biz.entity_id)
        adapter = XeroAdapter(cfg)
        pnl = adapter.pull_p_and_l(period.start, period.end)
        return RailResult(
            rail=rail.key, label=rail.label, basis=rail.basis,
            available=True, amount_cents=round(float(pnl.total_revenue) * 100),
            detail={"source": "Xero ProfitAndLoss report"},
        )
    except Exception as e:
        return RailResult(
            rail=rail.key, label=rail.label, basis=rail.basis,
            available=False, warning=str(e)[:160],
        )


_RAIL_IMPL = {
    "lemonsqueezy": _ls_revenue_rail,
    "figma_payouts": _figma_payout_rail,
    "xero_pnl": _xero_pnl_rail,
}


def cmd_revenue(args: argparse.Namespace) -> int:
    biz = get_business(args.business)
    lifetime_start = (
        date.fromisoformat(biz.first_revenue_date) if biz.first_revenue_date else None
    )
    period = parse_period(
        args.period, today=date.today(), lifetime_start=lifetime_start
    )
    answer = RevenueAnswer(business=biz.name, period_label=period.label)
    for rail in biz.rails:
        impl = _RAIL_IMPL[rail.key]
        answer.rails.append(impl(biz, rail, period))

    if args.json:
        print(json.dumps({
            "business": answer.business,
            "period": answer.period_label,
            "total_cents": answer.total_cents,
            "mixed_basis": answer.mixed_basis,
            "rails": [asdict(r) for r in answer.rails],
        }, indent=2))
    else:
        print(answer.to_text())
    return 0 if all(r.available for r in answer.rails) else 3


def cmd_mrr(args: argparse.Namespace) -> int:
    biz = get_business(args.business)
    if biz.key != "fon":
        print(f"MRR is only defined for subscription businesses (fon). {biz.name} has no MRR rail.")
        return 2
    adapter = LemonSqueezyAdapter(store_id=biz.ls_store_id, product_id=biz.ls_product_id)

    variants = adapter._paginate("/variants", {})
    vmap = {}
    for v in variants:
        a = v.get("attributes", {})
        vmap[int(v["id"])] = (a.get("price") or 0, a.get("interval") or "month")

    subs = adapter.pull_subscriptions(status_filter=None)
    mrr_cents = 0.0
    counts = {"annual": 0, "monthly": 0}
    for s in subs:
        a = s.get("attributes", {})
        if a.get("status") not in ("active", "past_due"):
            continue
        price_c, interval = vmap.get(a.get("variant_id"), (0, "month"))
        if interval == "year":
            mrr_cents += price_c / 12
            counts["annual"] += 1
        else:
            mrr_cents += price_c
            counts["monthly"] += 1

    out = {
        "business": biz.name,
        "mrr_usd": round(mrr_cents / 100, 2),
        "arr_usd": round(mrr_cents * 12 / 100, 2),
        "active_subs": counts["annual"] + counts["monthly"],
        "plan_mix": counts,
        "note": (
            "Interval-normalized (annual price / 12); includes past_due. "
            "Lemon Squeezy rail only — the Figma-native rail has no "
            "subscription API and is excluded from MRR."
        ),
    }
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(
            f"BUSINESS: {out['business']}\n"
            f"MRR:      ${out['mrr_usd']:,.2f}  (ARR ${out['arr_usd']:,.2f})\n"
            f"SUBS:     {out['active_subs']} active ({counts['annual']} annual, {counts['monthly']} monthly)\n"
            f"NOTE:     {out['note']}"
        )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="services.metrics", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("revenue", help="Revenue for a period, all registered rails")
    pr.add_argument("--business", required=True, help="fon | kal | dia")
    pr.add_argument("--period", required=True, help="lifetime | ytd | YYYY | YYYY-MM | YYYY-MM:YYYY-MM")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_revenue)

    pm = sub.add_parser("mrr", help="Current MRR (subscription businesses)")
    pm.add_argument("--business", required=True)
    pm.add_argument("--json", action="store_true")
    pm.set_defaults(func=cmd_mrr)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
