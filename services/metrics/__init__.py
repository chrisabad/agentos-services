"""Deterministic business metrics for Chris's portfolio companies.

Born from AGE-2787: three agent runs produced three contradictory Font
Replacer revenue numbers (cents read as dollars; orders-only sums missing
renewals; lifetime presented as YTD). Money math must run in tested code —
agents call this CLI and interpret its labeled output, never hand-compute.

Usage:
    python -m services.metrics revenue --business fon --period ytd
    python -m services.metrics revenue --business fon --period 2026-07 --json
    python -m services.metrics revenue --business kal --period 2026
    python -m services.metrics mrr --business fon

Every output states PERIOD, BASIS, and RAIL COVERAGE explicitly so a
partial answer can never masquerade as a total.
"""
