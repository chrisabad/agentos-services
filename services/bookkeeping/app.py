"""
Bookkeeping pipeline — service entry point.

Registers the bookkeeping pipeline as a FastAPI service under agentos-services.
Endpoints:
  GET /bookkeeping/health         — health check
  POST /bookkeeping/run           — run the pipeline for a given period
  GET /bookkeeping/invariants     — list available invariant checks
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from services.bookkeeping import run_bookkeeping_pipeline, get_run_logs, get_run_log


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Bookkeeping Pipeline",
    description="Invariant-gated bookkeeping close pipeline for KAL, FON, PER",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None
    entities: Optional[list[str]] = None


class HealthResponse(BaseModel):
    status: str
    service: str


class InvariantInfo(BaseModel):
    id: str
    name: str
    description: str
    severity: str
    blocking: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/bookkeeping/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", service="bookkeeping")


@app.get("/bookkeeping/invariants")
async def list_invariants():
    """List all available invariant checks with descriptions."""
    return [
        InvariantInfo(
            id="INV01",
            name="Balance Sheet Balances",
            description="Assets MUST equal Liabilities + Equity (within rounding threshold)",
            severity="error",
            blocking=True,
        ),
        InvariantInfo(
            id="INV02",
            name="Bank vs Ledger",
            description="Cash per Balance Sheet should match bank statement within threshold",
            severity="error",
            blocking=True,
        ),
        InvariantInfo(
            id="INV03",
            name="Unreconciled Count",
            description="Unreconciled transactions must be below per-entity threshold",
            severity="error",
            blocking=True,
        ),
        InvariantInfo(
            id="INV04",
            name="Month-over-Month Delta",
            description="Net income should not swing wildly without explanation",
            severity="warning",
            blocking=False,
        ),
        InvariantInfo(
            id="INV05",
            name="Out-of-Period Transactions",
            description="No transactions dated outside the reporting period",
            severity="error",
            blocking=True,
        ),
        InvariantInfo(
            id="INV06",
            name="Category Totals",
            description="No material uncategorized income/expense",
            severity="error",
            blocking=True,
        ),
        InvariantInfo(
            id="INV07",
            name="Prior Month Closed",
            description="Prior month must be closed before current month is signed off",
            severity="warning",
            blocking=False,
        ),
    ]


@app.get("/bookkeeping/runs")
async def list_runs(period: Optional[str] = None):
    """List past run logs, optionally filtered by period (YYYY-MM)."""
    return get_run_logs(period=period)


@app.get("/bookkeeping/runs/{period}/{timestamp:path}")
async def get_run(period: str, timestamp: str):
    """Retrieve a specific run log by period and timestamp."""
    log = get_run_log(period, timestamp)
    if log is None:
        raise HTTPException(status_code=404, detail="Run log not found")
    return log


@app.post("/bookkeeping/run")
async def run_pipeline(req: RunRequest):
    """
    Run the bookkeeping pipeline for the specified or default period.

    Returns a PipelineResult with:
      - period and all_passed status
      - per-entity invariant results (errors + warnings)
      - summary text
      - flagged transactions
    """
    try:
        result = run_bookkeeping_pipeline(
            year=req.year,
            month=req.month,
            entities=req.entities,
        )
        return {
            "period": result.period,
            "all_passed": result.all_passed,
            "summary": result.summary_text,
            "flags": result.flags,
            "entities": result.entity_reports,
            "log_path": result.log_path,
            "raw": result.raw.to_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
