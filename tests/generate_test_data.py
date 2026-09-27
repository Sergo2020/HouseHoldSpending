"""Create synthetic bank and card exports used by the regression suite."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from finance_dashboard.settings import DEFAULT_SETTINGS, SETTINGS_FILE


TEST_ROOT = PROJECT_ROOT / "test_data" / "users"


def write_excel(path: Path, sheets: dict[str, list[list[object]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, header=False, index=False)


def write_discount_bank(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["יום ערך", "תיאור התנועה", "זכות/חובה ₪", "ערוץ ביצוע"])
        writer.writerows(rows)


def write_settings() -> None:
    (TEST_ROOT / SETTINGS_FILE).write_text(
        json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    write_settings()
    write_discount_bank(TEST_ROOT / "member_1" / "bank" / "bank_export.csv", [
        ["01/01/2026", "Salary", 1000, "Transfer"],
        ["01/02/2026", "Local market", -50, "Card"],
    ])
    write_excel(TEST_ROOT / "member_1" / "credit_card" / "card_export.xlsx", {
        "Transactions": [
            ["Exported transactions"],
            ["תאריך עסקה", "שם בית עסק", 'סכום בש"ח', "סוג עסקה"],
            ["03/01/2026", "Market A", 100, "Regular"],
            ["03/02/2026", "Pharmacy A", 30, "Regular"],
        ],
    })

    write_excel(TEST_ROOT / "member_2" / "bank" / "bank_export.xlsx", {
        "Account": [
            ["Account activity"],
            ["תאריך ערך", "זכות", "חובה", "תיאור", "סוג פעולה"],
            ["01/01/2026", 1200, 0, "Salary", "Transfer"],
            ["02/02/2026", 0, 125, "חיוב לכרטיס ויזה 8373", "Card settlement"],
        ],
    })
    write_excel(TEST_ROOT / "member_2" / "credit_card" / "card_export.xlsx", {
        "Local": [
            ["תאריך עסקה", "שם בית העסק", "קטגוריה", "סכום חיוב"],
            ["04/01/2026", "Fuel Station", "Transport", 75],
            ["04/02/2026", "Book Store", "Shopping", 40],
        ],
        "Foreign": [
            ["תאריך עסקה", "שם בית העסק", "קטגוריה", "סכום חיוב"],
            ["10/01/2026", "International Store", "Online", 60],
        ],
    })

    write_discount_bank(TEST_ROOT / "member_3" / "bank" / "bank_export.csv", [
        ["05/01/2026", "Salary", 1100, "Transfer"],
        ["05/02/2026", "Utility payment", -80, "Transfer"],
    ])
    write_excel(TEST_ROOT / "member_3" / "credit_card" / "card_export.xlsx", {
        "Transactions": [
            ["תאריך עסקה", "שם בית עסק", 'סכום בש"ח', "סוג עסקה"],
            ["05/01/2026", "Market B", 90, "Regular"],
            ["06/02/2026", "Clinic B", 45, "Regular"],
        ],
    })


if __name__ == "__main__":
    main()
