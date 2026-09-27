from pathlib import Path
import unittest
from unittest.mock import patch
from copy import deepcopy

import pandas as pd

from finance_dashboard.analysis import classify, common_overlap, mark_new_spending_for_review
from finance_dashboard.data import (
    BenLeumiBankParser,
    DiscountBankParser,
    DiscountCardParser,
    MaxCardParser,
    deduplicate_transactions,
    identify_parser,
    load_transactions,
)
from finance_dashboard.settings import (
    BASE_RUBRICS,
    DEFAULT_SETTINGS,
    assign_rubric,
    move_to_junk,
    move_to_review,
    register_imported_transactions,
    restore_from_junk,
    SETTINGS_FILE,
    load_settings,
)


ROOT = Path(__file__).parents[1]
TEST_ROOT = ROOT / "test_data"


class DashboardTests(unittest.TestCase):
    def test_fixture_settings_are_user_owned_and_generic(self):
        settings_path = TEST_ROOT / "users" / SETTINGS_FILE
        self.assertTrue(settings_path.exists())
        self.assertEqual(load_settings(TEST_ROOT / "users")["rubrics"], BASE_RUBRICS)

    def test_missing_user_settings_request_a_generic_configuration(self):
        missing_root = TEST_ROOT / "missing_settings"
        with patch("finance_dashboard.settings.save_settings") as save_mock:
            settings = load_settings(missing_root)

        self.assertEqual(settings["rubrics"], ["Groceries", "Travel", "Rent", "Healthcare"])
        save_mock.assert_called_once_with(missing_root, settings)
    def test_all_six_synthetic_exports_import(self):
        transactions, warnings = load_transactions(TEST_ROOT)
        self.assertFalse(warnings, warnings)
        self.assertEqual(len(transactions), 13)
        self.assertEqual(set(transactions.user), {"member_1", "member_2", "member_3"})
        self.assertEqual(set(transactions.source), {"bank", "card"})
        self.assertIn("table_name", transactions.columns)
        self.assertFalse(transactions.table_name.isna().any())

    def test_export_parser_is_identified_from_table_structure(self):
        expected_parsers = {
            TEST_ROOT / "users" / "member_1" / "bank" / "bank_export.csv": DiscountBankParser,
            TEST_ROOT / "users" / "member_1" / "credit_card" / "card_export.xlsx": DiscountCardParser,
            TEST_ROOT / "users" / "member_2" / "bank" / "bank_export.xlsx": BenLeumiBankParser,
            TEST_ROOT / "users" / "member_2" / "credit_card" / "card_export.xlsx": MaxCardParser,
            TEST_ROOT / "users" / "member_3" / "bank" / "bank_export.csv": DiscountBankParser,
            TEST_ROOT / "users" / "member_3" / "credit_card" / "card_export.xlsx": DiscountCardParser,
        }

        for path, parser_type in expected_parsers.items():
            with self.subTest(path=path.name):
                self.assertIsInstance(identify_parser(path, "Any user"), parser_type)

    def test_foreign_currency_card_sheet_is_imported(self):
        transactions, warnings = load_transactions(TEST_ROOT)
        self.assertFalse(warnings, warnings)
        foreign_transactions = transactions[
            transactions.user.eq("member_2") & transactions.description.eq("International Store")
        ]
        self.assertFalse(foreign_transactions.empty)
        self.assertTrue(foreign_transactions.source.eq("card").all())
        self.assertTrue(foreign_transactions.amount.lt(0).all())

    def test_settlements_are_not_spending(self):
        transactions, _ = load_transactions(TEST_ROOT)
        settings = deepcopy(DEFAULT_SETTINGS)
        settlement_description = transactions.loc[
            transactions.description.astype(str).str.contains("8373"), "description"
        ].iloc[0]
        settings["settlement_patterns"] = [settlement_description]
        classified = classify(transactions, settings)
        self.assertGreater(classified.is_settlement.sum(), 0)
        self.assertFalse(classified.loc[classified.is_settlement, "is_spending"].any())

    def test_common_overlap_is_limited_by_all_users_and_sources(self):
        transactions, _ = load_transactions(TEST_ROOT)
        start, end = common_overlap(transactions)
        self.assertEqual(str(start.date()), "2026-01-05")
        self.assertEqual(str(end.date()), "2026-02-01")

    def test_junk_income_is_excluded_from_income_totals(self):
        transactions, _ = load_transactions(TEST_ROOT)
        baseline = classify(transactions, deepcopy(DEFAULT_SETTINGS))
        income_id = baseline.loc[baseline.is_income, "id"].iloc[0]
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["junk_ids"] = [income_id]
        classified = classify(transactions, settings)
        self.assertFalse(classified.loc[classified.id.eq(income_id), "is_income"].iloc[0])

    def test_manual_review_restores_an_excluded_settlement_to_spending(self):
        transactions = pd.DataFrame([{
            "id": "settlement-1", "date": pd.Timestamp("2026-01-01"), "user": "member_1",
            "description": "manual settlement", "amount": -100.0, "source": "bank", "raw_type": "",
        }])
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["settlement_patterns"] = ["manual settlement"]
        settings["needs_review_ids"] = ["settlement-1"]
        settings["manual_include_ids"] = ["settlement-1"]

        classified = classify(transactions, settings)

        self.assertFalse(classified.is_settlement.iloc[0])
        self.assertTrue(classified.is_spending.iloc[0])
        self.assertTrue(pd.isna(classified.rubric.iloc[0]))

    def test_manual_rubric_restores_an_excluded_transfer_to_spending(self):
        transactions = pd.DataFrame([{
            "id": "transfer-1", "date": pd.Timestamp("2026-01-01"), "user": "member_1",
            "description": "manual transfer", "amount": -100.0, "source": "bank", "raw_type": "",
        }])
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["household_transfer_patterns"] = ["manual transfer"]
        settings["manual_include_ids"] = ["transfer-1"]
        settings["transaction_overrides"] = {"transfer-1": "Groceries"}

        classified = classify(transactions, settings)

        self.assertFalse(classified.is_transfer.iloc[0])
        self.assertTrue(classified.is_spending.iloc[0])
        self.assertEqual(classified.rubric.iloc[0], "Groceries")

    def test_new_spending_requires_review_but_keeps_automatic_suggestion(self):
        transactions = pd.DataFrame([{
            "id": "new-spending", "date": pd.Timestamp("2026-01-01"), "user": "member_1",
            "description": "Known merchant", "amount": -100.0, "source": "card", "raw_type": "",
        }])
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["merchant_rules"] = {"known merchant": "Groceries"}

        classified = classify(transactions, settings)
        added_ids = mark_new_spending_for_review(classified, settings, {"new-spending"})
        reviewed = classify(transactions, settings)

        self.assertEqual(added_ids, {"new-spending"})
        self.assertTrue(pd.isna(reviewed.rubric.iloc[0]))
        self.assertEqual(reviewed.suggested_rubric.iloc[0], "Groceries")

    def test_manual_dispositions_keep_review_and_inclusion_flags_consistent(self):
        settings = deepcopy(DEFAULT_SETTINGS)

        move_to_review(settings, "transfer-1")
        self.assertIn("transfer-1", settings["needs_review_ids"])
        self.assertIn("transfer-1", settings["manual_include_ids"])
        self.assertNotIn("transfer-1", settings["reviewed_ids"])

        assign_rubric(settings, "transfer-1", "New rubric")
        self.assertEqual(settings["transaction_overrides"]["transfer-1"], "New rubric")
        self.assertIn("New rubric", settings["rubrics"])
        self.assertIn("transfer-1", settings["reviewed_ids"])
        self.assertIn("transfer-1", settings["manual_include_ids"])
        self.assertNotIn("transfer-1", settings["needs_review_ids"])

        move_to_junk(settings, "transfer-1")
        self.assertIn("transfer-1", settings["junk_ids"])
        self.assertNotIn("transfer-1", settings["needs_review_ids"])
        self.assertNotIn("transfer-1", settings["manual_include_ids"])

        restore_from_junk(settings, "transfer-1")
        self.assertNotIn("transfer-1", settings["junk_ids"])

    def test_first_seen_timestamp_is_kept_after_later_imports(self):
        settings = deepcopy(DEFAULT_SETTINGS)

        initial_new = register_imported_transactions(settings, ["first"], "2026-09-27T10:00:00+03:00")
        second_new = register_imported_transactions(settings, ["first", "second"], "2026-09-28T10:00:00+03:00")

        self.assertEqual(initial_new, set())
        self.assertEqual(second_new, {"second"})
        self.assertEqual(settings["transaction_first_seen"], {
            "first": "2026-09-27T10:00:00+03:00",
            "second": "2026-09-28T10:00:00+03:00",
        })

    def test_initial_tracking_baseline_does_not_create_review_items(self):
        settings = deepcopy(DEFAULT_SETTINGS)
        settings["transaction_first_seen"] = {"old-spending": "2026-09-27T10:00:00+03:00"}
        settings["needs_review_ids"] = ["old-spending"]

        new_ids = register_imported_transactions(settings, ["old-spending"], "2026-09-28T10:00:00+03:00")

        self.assertEqual(new_ids, set())
        self.assertEqual(settings["needs_review_ids"], [])
        self.assertTrue(settings["import_tracking_initialized"])

    def test_overlapping_exports_are_deduplicated(self):
        transactions, _ = load_transactions(TEST_ROOT)
        duplicate = transactions.iloc[[0]].copy()
        duplicate["id"] = "different-export-row-id"
        first = transactions.iloc[[0]].copy()
        first["_import_file"] = "older.xlsx"
        duplicate["_import_file"] = "newer.xlsx"
        merged = deduplicate_transactions(pd.concat([first, duplicate], ignore_index=True))
        self.assertEqual(len(merged), 1)

    def test_repeats_inside_one_export_are_preserved(self):
        transactions, _ = load_transactions(TEST_ROOT)
        repeated = pd.concat([transactions.iloc[[0]], transactions.iloc[[0]]], ignore_index=True)
        repeated["_import_file"] = "single-export.xlsx"
        self.assertEqual(len(deduplicate_transactions(repeated)), 2)


if __name__ == "__main__":
    unittest.main()
