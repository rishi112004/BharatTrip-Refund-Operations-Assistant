"""
tests/test_bharattrip_reconciliation.py
----------------------------------------
Automated test suite for BharatTrip deterministic reconciliation engine.
Tests normalization, duplicate detection, ambiguous references, date parsing,
amount analysis, explained deductions, missing deductions, explicit zero deductions,
and actual workbook benchmark cases.
"""

import os
import sys
import tempfile
import unittest
import pandas as pd

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from bharattrip_reconciliation import (
    BharatTripReconciliationEngine,
    normalize_reference,
    normalize_date,
    clean_status,
    get_default_report_path,
)


class TestReferenceNormalization(unittest.TestCase):
    """Test string reference normalization rules."""

    def test_standard_format(self):
        res = normalize_reference("RF-1286")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.normalized, "RF-1286")

    def test_case_variation(self):
        res = normalize_reference("rf-1650")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.normalized, "RF-1650")

    def test_space_instead_of_hyphen(self):
        res = normalize_reference("RF 1750")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.normalized, "RF-1750")

    def test_no_delimiter(self):
        res = normalize_reference("RF1109")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.normalized, "RF-1109")

    def test_revised_suffix(self):
        res = normalize_reference("RF-1313 (revised)")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.normalized, "RF-1313")
        self.assertTrue(res.is_revised)

    def test_concatenated_agent_name(self):
        res = normalize_reference("RF-1175, Orbit Travels")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.normalized, "RF-1175")
        self.assertEqual(res.extra_text, "Orbit Travels")

    def test_leading_trailing_whitespace(self):
        res = normalize_reference("   RF-1030   ")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.normalized, "RF-1030")

    def test_ambiguous_multiple_references(self):
        res = normalize_reference("RF-1001, RF-1002")
        self.assertFalse(res.is_valid)
        self.assertTrue(res.is_ambiguous)
        self.assertIn("RF-1001", res.candidate_refs)
        self.assertIn("RF-1002", res.candidate_refs)

    def test_empty_and_placeholders(self):
        for placeholder in ["", "   ", "-", "no ref", "not known", None]:
            res = normalize_reference(placeholder)
            self.assertFalse(res.is_valid)
            self.assertIsNone(res.normalized)


class TestDateNormalization(unittest.TestCase):
    """Test date parsing across multiple text and timestamp formats."""

    def test_iso_string(self):
        self.assertEqual(normalize_date("2026-05-25"), "2026-05-25")
        self.assertEqual(normalize_date("2026-05-25 00:00:00"), "2026-05-25")

    def test_dd_mm_yyyy(self):
        self.assertEqual(normalize_date("09-06-2026"), "2026-06-09")

    def test_dd_mm_yy_slash(self):
        self.assertEqual(normalize_date("30/05/26"), "2026-05-30")

    def test_dd_mm_yyyy_slash(self):
        self.assertEqual(normalize_date("19/03/2026"), "2026-03-19")

    def test_invalid_dates(self):
        self.assertIsNone(normalize_date("-"))
        self.assertIsNone(normalize_date(None))
        self.assertIsNone(normalize_date("invalid_date_text"))


class TestReconciliationEngineWorkbookCases(unittest.TestCase):
    """Test against actual BharatTrip workbook data for the core benchmark cases."""

    @classmethod
    def setUpClass(cls):
        cls.engine = BharatTripReconciliationEngine()

    def test_rf_1570_missing_in_finance(self):
        res = self.engine.reconcile_reference("RF-1570")
        self.assertEqual(res.primary_category, "MISSING_IN_FINANCE")
        self.assertTrue(res.needs_human_review)
        self.assertEqual(len(res.support_rows), 1)
        self.assertEqual(len(res.finance_rows), 0)

    def test_rf_1757_closed_but_pending(self):
        res = self.engine.reconcile_reference("RF-1757")
        self.assertEqual(res.primary_category, "CLOSED_BUT_PENDING")
        self.assertTrue(res.needs_human_review)
        self.assertEqual(res.support_status, "Closed")
        self.assertEqual(res.finance_payout_status, "Pending Payout")

    def test_rf_1527_closed_but_declined(self):
        res = self.engine.reconcile_reference("RF-1527")
        self.assertEqual(res.primary_category, "CLOSED_BUT_DECLINED")
        self.assertTrue(res.needs_human_review)
        self.assertEqual(res.support_status, "Closed")
        self.assertEqual(res.finance_payout_status, "Declined")

    def test_rf_1148_duplicate_in_finance(self):
        res = self.engine.reconcile_reference("RF-1148")
        self.assertEqual(res.primary_category, "DUPLICATE_IN_FINANCE")
        self.assertTrue(res.needs_human_review)
        self.assertEqual(len(res.finance_rows), 2)
        self.assertIn("DUPLICATE_IN_FINANCE", res.issue_flags)

    def test_rf_1252_explained_deduction(self):
        res = self.engine.reconcile_reference("RF-1252")
        self.assertEqual(res.primary_category, "EXPLAINED_DEDUCTION")
        self.assertTrue(res.is_deduction_explained)
        self.assertEqual(res.amount_difference, 1700.0)
        self.assertEqual(res.finance_deduction, 1700.0)
        self.assertEqual(res.deduction_status, "RECORDED_POSITIVE")
        self.assertIn("UNVERIFIED_POLICY_AUTHORIZATION", res.issue_flags)

    def test_rf_1232_multiple_issue_flags(self):
        res = self.engine.reconcile_reference("RF-1232")
        # Primary is PAID_BUT_SUPPORT_WORKING, secondary preserves SUPPORT_DUPLICATE_REVIEW
        self.assertEqual(res.primary_category, "PAID_BUT_SUPPORT_WORKING")
        self.assertEqual(res.finance_payout_status, "Refund Done")
        self.assertIn("PAID_BUT_SUPPORT_WORKING", res.issue_flags)
        self.assertIn("SUPPORT_DUPLICATE_REVIEW", res.issue_flags)
        self.assertIn("SUPPORT_CONFLICTING_DUPLICATE", res.issue_flags)

    def test_rf_1181_conflicting_support_duplicates(self):
        res = self.engine.reconcile_reference("RF-1181")
        self.assertEqual(res.primary_category, "SUPPORT_DUPLICATE_REVIEW")
        self.assertTrue(res.needs_human_review)
        self.assertIn("SUPPORT_CONFLICTING_DUPLICATE", res.issue_flags)
        self.assertIn("SUPPORT_DUPLICATE_REVIEW", res.issue_flags)

    def test_rf_1110_finance_only_record(self):
        res = self.engine.reconcile_reference("RF-1110")
        self.assertEqual(res.primary_category, "FINANCE_ONLY_RECORDS")
        self.assertTrue(res.needs_human_review)
        self.assertEqual(len(res.support_rows), 0)
        self.assertEqual(len(res.finance_rows), 1)


class TestDeductionsAndVarianceEdgeCases(unittest.TestCase):
    """Test detailed deduction states: explained, missing, explicit zero, and unexplained."""

    @classmethod
    def setUpClass(cls):
        cls.engine = BharatTripReconciliationEngine()

    def test_explicit_zero_deduction_audit(self):
        # In RF-1286, deduction is explicitly 0.0 and amount difference is 0.0
        res = self.engine.reconcile_reference("RF-1286")
        self.assertEqual(res.deduction_status, "EXPLICIT_ZERO")
        self.assertEqual(res.finance_deduction, 0.0)
        self.assertEqual(res.primary_category, "CLEAN_MATCH_REFUNDED")

    def test_missing_deduction_audit(self):
        # In RF-1796, Deduction (INR) is blank (NaN), but paid equals claimed
        res = self.engine.reconcile_reference("RF-1796")
        self.assertEqual(res.deduction_status, "MISSING")
        self.assertIsNone(res.finance_deduction)
        self.assertEqual(res.primary_category, "CLEAN_MATCH_REFUNDED")

    def test_recorded_positive_deduction_audit(self):
        # In RF-1298, Support=15400, Paid=13900, Deduction=1500
        res = self.engine.reconcile_reference("RF-1298")
        self.assertEqual(res.deduction_status, "RECORDED_POSITIVE")
        self.assertEqual(res.finance_deduction, 1500.0)
        self.assertEqual(res.amount_difference, 1500.0)
        self.assertTrue(res.is_deduction_explained)
        self.assertEqual(res.primary_category, "EXPLAINED_DEDUCTION")

    def test_all_finance_duplicates_identified(self):
        # Exactly 9 Finance references are duplicated
        fin_dups = [
            "RF-1148", "RF-1177", "RF-1238", "RF-1393",
            "RF-1395", "RF-1545", "RF-1606", "RF-1672", "RF-1851"
        ]
        for ref in fin_dups:
            res = self.engine.reconcile_reference(ref)
            self.assertEqual(res.primary_category, "DUPLICATE_IN_FINANCE")
            self.assertTrue(res.needs_human_review)
            self.assertIn("DUPLICATE_IN_FINANCE", res.issue_flags)
            self.assertGreaterEqual(len(res.finance_rows), 2)


class TestReconciliationReportGeneration(unittest.TestCase):
    """Test output report generation, format routing, and directory creation without workbook modification."""

    def test_default_report_path_no_args(self):
        """Default call with no arguments must yield a valid CSV path ending in .csv."""
        path = get_default_report_path()
        self.assertTrue(path.endswith(".csv"), f"Expected .csv path, got: {path}")
        self.assertIn("BharatTrip_Reconciliation_Report.csv", path)

    def test_default_report_path_csv_variants(self):
        """Format strings 'csv', '.csv', 'CSV' must all resolve to the CSV path."""
        for fmt in ["csv", ".csv", "CSV", " Csv "]:
            path = get_default_report_path(fmt)
            self.assertTrue(path.endswith(".csv"), f"Failed for format '{fmt}': {path}")

    def test_default_report_path_xlsx_variants(self):
        """Format strings 'xlsx', '.xlsx', 'excel', 'XLSX' must all resolve to the XLSX path."""
        for fmt in ["xlsx", ".xlsx", "excel", "EXCEL", " XLSX "]:
            path = get_default_report_path(fmt)
            self.assertTrue(path.endswith(".xlsx"), f"Failed for format '{fmt}': {path}")

    def test_default_report_path_invalid_format(self):
        """Unsupported formats (json, pdf, docx, etc.) must raise an explicit ValueError."""
        for bad_fmt in ["json", "pdf", "txt", "html", "parquet"]:
            with self.assertRaises(ValueError) as ctx:
                get_default_report_path(bad_fmt)
            self.assertIn("Unsupported report format", str(ctx.exception))

    def test_generate_report_csv_and_excel(self):
        """Verify report generation produces both a valid CSV and Excel file with 749 rows."""
        csv_file = get_default_report_path("csv")
        excel_file = get_default_report_path("xlsx")
        engine = BharatTripReconciliationEngine()
        df_report = engine.generate_reconciliation_report(csv_file, export_excel=True)

        self.assertTrue(os.path.exists(csv_file), "CSV file was not created")
        self.assertTrue(os.path.exists(excel_file), "Excel file was not created")
        self.assertEqual(len(df_report), 749)

        # Verify CSV can be read back cleanly
        df_csv = pd.read_csv(csv_file)
        self.assertEqual(len(df_csv), 749)
        self.assertIn("Normalized Reference", df_csv.columns)
        self.assertIn("Primary Category", df_csv.columns)
        self.assertIn("Deduction Status", df_csv.columns)
        self.assertIn("Needs Human Review", df_csv.columns)
        self.assertIn("Amount Difference (INR)", df_csv.columns)

        # Verify Excel can be read back cleanly
        df_excel = pd.read_excel(excel_file, engine="openpyxl")
        self.assertEqual(len(df_excel), 749)
        self.assertListEqual(list(df_csv["Normalized Reference"]), list(df_excel["Normalized Reference"]))

    def test_export_auto_creates_missing_directory(self):
        """Exporting to a non-existent directory must auto-create parent directories gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = os.path.join(tmpdir, "nested", "reports")
            target_csv = os.path.join(nested_dir, "test_report.csv")
            self.assertFalse(os.path.exists(nested_dir))

            engine = BharatTripReconciliationEngine()
            df = engine.generate_reconciliation_report(target_csv, export_excel=True)

            self.assertTrue(os.path.exists(nested_dir), "Parent directory was not created")
            self.assertTrue(os.path.exists(target_csv), "Target CSV was not created")
            target_xlsx = target_csv.replace(".csv", ".xlsx")
            self.assertTrue(os.path.exists(target_xlsx), "Target XLSX was not created")
            self.assertEqual(len(df), 749)


if __name__ == "__main__":
    unittest.main()
