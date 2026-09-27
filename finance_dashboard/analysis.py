from __future__ import annotations

import re
import pandas as pd


def _matches(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _rubric(description: str, transaction_id: str, settings: dict, allow_review: bool = False) -> str | None:
    # A manual Review decision takes precedence over a merchant rule or an
    # earlier per-transaction assignment.
    if not allow_review and transaction_id in settings.get("needs_review_ids", []):
        return None
    if transaction_id in settings["transaction_overrides"]:
        return settings["transaction_overrides"][transaction_id]
    for merchant, rubric in settings["merchant_rules"].items():
        if merchant.lower() in description.lower():
            return rubric
    return None


def classify(transactions: pd.DataFrame, settings: dict) -> pd.DataFrame:
    result = transactions.copy()
    if result.empty:
        return result
    manual_include = result.id.isin(settings.get("manual_include_ids", []))
    result["is_settlement"] = result.apply(
        lambda row: row.source == "bank" and _matches(row.description, settings["settlement_patterns"]), axis=1
    ) & ~manual_include
    result["is_transfer"] = result.apply(
        lambda row: row.source == "bank" and _matches(row.description, settings["household_transfer_patterns"]), axis=1
    ) & ~manual_include
    result["is_junk"] = result.id.isin(settings["junk_ids"])
    result["is_reviewed"] = result.id.isin(settings.get("reviewed_ids", []))
    merchant_counts = result.assign(merchant=result.description.str.lower()).groupby("merchant").size()
    result["is_one_off"] = result.description.str.lower().map(merchant_counts).eq(1)
    refund_or_interest = result.description.str.contains("זיכוי|החזר|ריבית|יתרת זכות", case=False, regex=True, na=False)
    result["is_income"] = (result.source.eq("bank") & result.amount.gt(0) & ~result.is_transfer & ~refund_or_interest & ~result.is_junk)
    result["is_spending"] = result.amount.lt(0) & ~result.is_settlement & ~result.is_transfer & ~result.is_junk
    result["suggested_rubric"] = result.apply(
        lambda row: _rubric(row.description, row.id, settings, allow_review=True)
        if row.is_spending or row.is_income else None,
        axis=1,
    )
    result["rubric"] = result.apply(
        lambda row: _rubric(row.description, row.id, settings) if row.is_spending or row.is_income else None,
        axis=1,
    )
    return result


def mark_new_spending_for_review(classified: pd.DataFrame, settings: dict, new_ids: set[str]) -> set[str]:
    """Require explicit review for newly imported spending, including auto-rubric matches."""
    new_spending_ids = set(classified.loc[
        classified.id.isin(new_ids) & classified.is_spending, "id"
    ])
    needs_review_ids = set(settings.get("needs_review_ids", []))
    added_ids = new_spending_ids - needs_review_ids
    if added_ids:
        settings["needs_review_ids"] = list(needs_review_ids | added_ids)
    return added_ids


def week_start(series: pd.Series) -> pd.Series:
    return series - pd.to_timedelta(series.dt.weekday, unit="D")


def common_overlap(transactions: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the date range present in every user/source export."""
    ranges = transactions.groupby(["user", "source"]).date.agg(["min", "max"])
    if ranges.empty:
        raise ValueError("Cannot calculate an overlap without transactions")
    start, end = ranges["min"].max(), ranges["max"].min()
    if start > end:
        raise ValueError("The four exports do not have a common date range")
    return start, end
