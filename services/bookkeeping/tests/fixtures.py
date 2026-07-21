"""
Test fixtures for Xero report parsing.

These simulate the actual Xero API response structure for both standard
(FON-like) and non-standard (KAL-like) chart layouts.
"""

from __future__ import annotations

from typing import Any, Dict, List


# =========================================================================
# KAL-like P&L — non-standard section titles, combined sections
# =========================================================================
#
# KAL's Xero P&L has:
#   - No "Revenue" or "Income" section title (empty or non-standard)
#   - A combined "Other Income and Expense" section
#   - Negative expense line items
#   - Nested sub-sections

def kal_p_and_l() -> Dict[str, Any]:
    """Simulate KAL's P&L report with non-standard structure.

    Expected totals: Revenue=$5000.00, Expenses=$2876.54
    """
    return {
        "ReportID": "ProfitAndLoss",
        "ReportName": "Profit and Loss",
        "ReportTitles": ["Profit and Loss", "Kaleidoscope", "1 July 2026 to 31 July 2026"],
        "Rows": [
            {
                "RowType": "Section",
                "Title": "",  # Empty title — no "Revenue" or "Income" keyword
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Sales", "Attributes": [{"Value": "200"}]},
                            {"Value": "5000.00"},
                        ],
                    },
                ],
            },
            {
                "RowType": "Section",
                "Title": "Other Income and Expense",  # Combined section
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Interest Income", "Attributes": [{"Value": "700"}]},
                            {"Value": "0.00"},
                        ],
                    },
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Bank Fees", "Attributes": [{"Value": "6030"}]},
                            {"Value": "-16.00"},  # Negative value
                        ],
                    },
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Software Subscriptions", "Attributes": [{"Value": "6340"}]},
                            {"Value": "-74.00"},  # Negative value
                        ],
                    },
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Other Expenses", "Attributes": [{"Value": "7000"}]},
                            {"Value": "-2786.54"},  # Negative value
                        ],
                    },
                ],
            },
        ],
    }


# =========================================================================
# FON-like P&L — standard section titles (regression fixture)
# =========================================================================

def fon_p_and_l() -> Dict[str, Any]:
    """Simulate FON's P&L report with standard structure.

    Expected totals: Revenue=$12000.00, Expenses=$4500.00
    """
    return {
        "ReportID": "ProfitAndLoss",
        "ReportName": "Profit and Loss",
        "ReportTitles": ["Profit and Loss", "Font Replacer", "1 July 2026 to 31 July 2026"],
        "Rows": [
            {
                "RowType": "Section",
                "Title": "Revenue",
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Sales", "Attributes": [{"Value": "200"}]},
                            {"Value": "10000.00"},
                        ],
                    },
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Consulting", "Attributes": [{"Value": "210"}]},
                            {"Value": "2000.00"},
                        ],
                    },
                ],
            },
            {
                "RowType": "Section",
                "Title": "Expenses",
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Software", "Attributes": [{"Value": "461"}]},
                            {"Value": "2500.00"},
                        ],
                    },
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Office Supplies", "Attributes": [{"Value": "500"}]},
                            {"Value": "500.00"},
                        ],
                    },
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Contractors", "Attributes": [{"Value": "600"}]},
                            {"Value": "1500.00"},
                        ],
                    },
                ],
            },
        ],
    }


# =========================================================================
# KAL-like Balance Sheet — nested sub-sections
# =========================================================================
#
# KAL's Xero Balance Sheet has:
#   - Assets section with nested sub-sections (Current Assets, Non-Current Assets)
#   - Each sub-section has its own Rows with line items
#   - The sub-section headers themselves have Cells with totals

def kal_balance_sheet() -> Dict[str, Any]:
    """Simulate KAL's Balance Sheet with nested sub-sections.

    Expected totals: Assets=$2876.54, Liabilities=$7980.28, Equity=$-5103.74
    """
    return {
        "ReportID": "BalanceSheet",
        "ReportName": "Balance Sheet",
        "ReportTitles": ["Balance Sheet", "Kaleidoscope", "As at 31 July 2026"],
        "Rows": [
            {
                "RowType": "Section",
                "Title": "Assets",
                "Rows": [
                    {
                        "RowType": "Section",
                        "Title": "Current Assets",
                        "Rows": [
                            {
                                "RowType": "Row",
                                "Cells": [
                                    {"Value": "Business Checking Account", "Attributes": [{"Value": "090"}]},
                                    {"Value": "2876.54"},
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "RowType": "Section",
                "Title": "Liabilities",
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Accounts Payable", "Attributes": [{"Value": "2000"}]},
                            {"Value": "7980.28"},
                        ],
                    },
                ],
            },
            {
                "RowType": "Section",
                "Title": "Equity",
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Retained Earnings", "Attributes": [{"Value": "3000"}]},
                            {"Value": "-5103.74"},
                        ],
                    },
                ],
            },
        ],
    }


# =========================================================================
# FON-like Balance Sheet — flat structure (regression fixture)
# =========================================================================

def fon_balance_sheet() -> Dict[str, Any]:
    """Simulate FON's Balance Sheet with flat structure.

    Expected totals: Assets=$25000.00, Liabilities=$10000.00, Equity=$15000.00
    """
    return {
        "ReportID": "BalanceSheet",
        "ReportName": "Balance Sheet",
        "ReportTitles": ["Balance Sheet", "Font Replacer", "As at 31 July 2026"],
        "Rows": [
            {
                "RowType": "Section",
                "Title": "Assets",
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Cash", "Attributes": [{"Value": "090"}]},
                            {"Value": "15000.00"},
                        ],
                    },
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Accounts Receivable", "Attributes": [{"Value": "110"}]},
                            {"Value": "10000.00"},
                        ],
                    },
                ],
            },
            {
                "RowType": "Section",
                "Title": "Liabilities",
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Accounts Payable", "Attributes": [{"Value": "2000"}]},
                            {"Value": "10000.00"},
                        ],
                    },
                ],
            },
            {
                "RowType": "Section",
                "Title": "Equity",
                "Rows": [
                    {
                        "RowType": "Row",
                        "Cells": [
                            {"Value": "Owner's Equity", "Attributes": [{"Value": "3000"}]},
                            {"Value": "15000.00"},
                        ],
                    },
                ],
            },
        ],
    }
