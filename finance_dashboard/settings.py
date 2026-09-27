from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


SETTINGS_FILE = ".dashboard_settings.json"
BASE_RUBRICS = ["Groceries", "Travel", "Rent", "Healthcare"]
DEFAULT_SETTINGS = {
    "rubrics": BASE_RUBRICS,
    "merchant_rules": {},
    "transaction_overrides": {},
    "reviewed_ids": [],
    "needs_review_ids": [],
    "manual_include_ids": [],
    "junk_ids": [],
    "transaction_first_seen": {},
    "import_tracking_initialized": False,
    "settlement_patterns": [],
    "household_transfer_patterns": [],
    "selected_rubric": None,
}


def load_settings(root: Path) -> dict:
    """Load user-owned settings, creating a generic configuration if needed."""
    path = root / SETTINGS_FILE
    if not path.exists():
        settings = deepcopy(DEFAULT_SETTINGS)
        save_settings(root, settings)
        return settings
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(DEFAULT_SETTINGS)
    settings = deepcopy(DEFAULT_SETTINGS)
    settings.update(saved)
    return settings


def save_settings(root: Path, settings: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / SETTINGS_FILE).write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def add_rubric(settings: dict, rubric: str) -> None:
    """Add a rubric once while preserving the configured display order."""
    if rubric not in settings["rubrics"]:
        settings["rubrics"].append(rubric)


def assign_rubric(settings: dict, transaction_id: str, rubric: str) -> None:
    """Classify a transaction and explicitly include it in reporting."""
    add_rubric(settings, rubric)
    settings["transaction_overrides"][transaction_id] = rubric
    _update_transaction_sets(settings, transaction_id, junk=False, reviewed=True, needs_review=False, manual_include=True)


def move_to_review(settings: dict, transaction_id: str) -> None:
    """Require a decision for a transaction, including excluded bank rows."""
    settings["transaction_overrides"].pop(transaction_id, None)
    _update_transaction_sets(settings, transaction_id, junk=False, reviewed=False, needs_review=True, manual_include=True)


def move_to_junk(settings: dict, transaction_id: str) -> None:
    """Exclude a transaction from reports and clear any outstanding review."""
    _update_transaction_sets(settings, transaction_id, junk=True, reviewed=True, needs_review=False, manual_include=False)


def restore_from_junk(settings: dict, transaction_id: str) -> None:
    """Restore a Junk item without changing its existing classification."""
    _update_transaction_sets(settings, transaction_id, junk=False)


def _update_transaction_sets(settings: dict, transaction_id: str, **memberships: bool) -> None:
    """Update list-backed transaction flags through one consistent contract."""
    fields = {
        "junk": "junk_ids",
        "reviewed": "reviewed_ids",
        "needs_review": "needs_review_ids",
        "manual_include": "manual_include_ids",
    }
    for membership, should_include in memberships.items():
        values = set(settings.get(fields[membership], []))
        if should_include:
            values.add(transaction_id)
        else:
            values.discard(transaction_id)
        settings[fields[membership]] = list(values)


def register_imported_transactions(settings: dict, transaction_ids: list[str], recorded_at: str) -> set[str]:
    """Record a transaction's first appearance and return IDs newly added after the baseline."""
    first_seen = settings.setdefault("transaction_first_seen", {})
    if not settings.get("import_tracking_initialized", False):
        if first_seen and len(set(first_seen.values())) == 1:
            first_seen_ids = set(first_seen)
            settings["needs_review_ids"] = [
                transaction_id for transaction_id in settings.get("needs_review_ids", [])
                if transaction_id not in first_seen_ids
            ]
        for transaction_id in transaction_ids:
            first_seen.setdefault(transaction_id, recorded_at)
        settings["import_tracking_initialized"] = True
        return set()

    new_ids = set()
    for transaction_id in transaction_ids:
        if transaction_id not in first_seen:
            first_seen[transaction_id] = recorded_at
            new_ids.add(transaction_id)
    return new_ids
