"""
bharattrip_reconciliation.py
-----------------------------
Deterministic financial reconciliation engine for BharatTrip refund operations.
Processes operational records from Excel without modifying the source workbook.
Handles reference normalization, duplicate detection, cross-department matching,
amount variance analysis (explained deductions vs unverified differences), and audit trail extraction.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ==============================================================================
# 1. REFERENCE NORMALIZATION
# ==============================================================================

@dataclass
class NormalizedReference:
    """Detailed metadata about a normalized reference."""
    original: str
    normalized: Optional[str]
    is_valid: bool
    is_ambiguous: bool = False
    is_revised: bool = False
    candidate_refs: List[str] = field(default_factory=list)
    extra_text: Optional[str] = None


def normalize_reference(raw_ref: Any) -> NormalizedReference:
    """
    Normalizes a refund reference string.
    
    Handles:
      - Case variations: rf-1650 -> RF-1650
      - Spacing and punctuation: RF 1750, RF1109, RF-1286 -> RF-1750, RF-1109, RF-1286
      - Revisions: RF-1313 (revised) -> RF-1313 (flagged as revised)
      - Concatenated agent/free-text: 'RF-1175, Orbit Travels' -> RF-1175
      - Multiple references: 'RF-1001, RF-1002' -> Flagged as ambiguous
      - Empty/placeholder: '', '-', 'no ref', 'not known', NaN -> is_valid=False
    """
    if raw_ref is None or pd.isna(raw_ref):
        return NormalizedReference(
            original="",
            normalized=None,
            is_valid=False,
            candidate_refs=[]
        )

    orig_str = str(raw_ref).strip()
    if orig_str.lower() in {"", "nan", "none", "-", "no ref", "not known", "null"}:
        return NormalizedReference(
            original=orig_str,
            normalized=None,
            is_valid=False,
            candidate_refs=[]
        )

    # Detect revision tag
    is_revised = bool(re.search(r"\(revised\)", orig_str, flags=re.IGNORECASE))
    clean_text = re.sub(r"\(revised\)", "", orig_str, flags=re.IGNORECASE).strip()

    # Search for all RF-style reference patterns: RF, rf, RF-, RF , RF1234
    matches = re.findall(r"\bRF\s*[-]?\s*(\d+)\b", clean_text, flags=re.IGNORECASE)

    # Extract extra text outside the reference
    extra_text = None
    if matches:
        text_without_rf = re.sub(r"\bRF\s*[-]?\s*\d+\b", "", clean_text, flags=re.IGNORECASE)
        text_without_rf = re.sub(r"[,;/|]", "", text_without_rf).strip()
        if text_without_rf:
            extra_text = text_without_rf

    unique_candidates = sorted(list({f"RF-{m}" for m in matches}))

    if len(unique_candidates) == 0:
        return NormalizedReference(
            original=orig_str,
            normalized=None,
            is_valid=False,
            is_revised=is_revised,
            candidate_refs=[],
            extra_text=clean_text
        )

    if len(unique_candidates) > 1:
        return NormalizedReference(
            original=orig_str,
            normalized=None,
            is_valid=False,
            is_ambiguous=True,
            is_revised=is_revised,
            candidate_refs=unique_candidates,
            extra_text=extra_text
        )

    norm_ref = unique_candidates[0]
    return NormalizedReference(
        original=orig_str,
        normalized=norm_ref,
        is_valid=True,
        is_ambiguous=False,
        is_revised=is_revised,
        candidate_refs=[norm_ref],
        extra_text=extra_text
    )


# ==============================================================================
# 2. DATE & STATUS NORMALIZATION HELPERS
# ==============================================================================

def normalize_date(raw_date: Any) -> Optional[str]:
    """
    Parses dates in mixed formats without modifying original strings:
      - datetime / timestamp objects
      - YYYY-MM-DD / YYYY-MM-DD HH:MM:SS
      - DD-MM-YYYY
      - DD/MM/YY
      - DD/MM/YYYY
    Returns ISO date string 'YYYY-MM-DD' or None if invalid/missing.
    """
    if raw_date is None or pd.isna(raw_date):
        return None

    if isinstance(raw_date, (datetime, pd.Timestamp)):
        return raw_date.strftime("%Y-%m-%d")

    s = str(raw_date).strip()
    if not s or s.lower() in {"nan", "none", "null", "-"}:
        return None

    date_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d/%m/%Y",
    ]
    for fmt in date_formats:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year < 100:
                dt = dt.replace(year=dt.year + 2000)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def clean_status(raw_status: Any) -> str:
    """Strips whitespace and handles missing status values."""
    if raw_status is None or pd.isna(raw_status):
        return "UNKNOWN"
    return str(raw_status).strip()


# ==============================================================================
# 3. RECONCILIATION RESULT DATA MODEL
# ==============================================================================

@dataclass
class ReconciliationItem:
    """Complete, structured reconciliation breakdown for a refund record."""
    normalized_reference: str
    original_references: List[str]
    support_rows: List[int]
    finance_rows: List[int]
    escalation_rows: List[int]

    # Statuses
    support_status: str
    finance_payout_status: str
    escalation_status: Optional[str]

    # Financials
    support_amount: Optional[float]
    finance_amount_paid: Optional[float]
    finance_deduction: Optional[float]
    deduction_status: str  # 'EXPLICIT_ZERO', 'RECORDED_POSITIVE', 'MISSING'
    amount_difference: Optional[float]
    is_deduction_explained: bool

    # Classifications
    primary_category: str
    issue_flags: List[str]
    needs_human_review: bool
    human_review_reasons: List[str]
    explanation: str

    # Agent & Route details
    agent_name: Optional[str] = None
    route: Optional[str] = None
    finance_remarks: Optional[str] = None
    support_notes: Optional[str] = None
    approved_by: Optional[str] = None
    finance_received_on: Optional[str] = None
    finance_processed_on: Optional[str] = None
    escalation_id: Optional[str] = None
    escalation_complaint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Normalized Reference": self.normalized_reference,
            "Original Reference": "; ".join(self.original_references),
            "Primary Category": self.primary_category,
            "Needs Human Review": self.needs_human_review,
            "Support Rows": "; ".join(map(str, self.support_rows)),
            "Finance Rows": "; ".join(map(str, self.finance_rows)),
            "Escalation Rows": "; ".join(map(str, self.escalation_rows)),
            "Support Status": self.support_status,
            "Finance Payout Status": self.finance_payout_status,
            "Escalation Status": self.escalation_status,
            "Escalation ID": self.escalation_id,
            "Escalation Complaint": self.escalation_complaint,
            "Support Amount (INR)": self.support_amount,
            "Finance Amount Paid (INR)": self.finance_amount_paid,
            "Finance Deduction (INR)": self.finance_deduction,
            "Deduction Status": self.deduction_status,
            "Amount Difference (INR)": self.amount_difference,
            "Is Deduction Explained": self.is_deduction_explained,
            "Issue Flags": ", ".join(self.issue_flags),
            "Human Review Reasons": "; ".join(self.human_review_reasons),
            "Explanation": self.explanation,
            "Agent Name": self.agent_name,
            "Route": self.route,
            "Finance Remarks": self.finance_remarks,
            "Support Notes": self.support_notes,
            "Approved By": self.approved_by,
            "Received On": self.finance_received_on,
            "Processed On": self.finance_processed_on,
        }


# ==============================================================================
# 4. RECONCILIATION ENGINE
# ==============================================================================

def get_default_workbook_path() -> str:
    """Finds the default workbook path relative to CWD or package root."""
    if os.path.exists("data/BharatTrip_Refund_Data.xlsx"):
        return "data/BharatTrip_Refund_Data.xlsx"
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(pkg_root, "data", "BharatTrip_Refund_Data.xlsx")
    if os.path.exists(candidate):
        return candidate
    return "data/BharatTrip_Refund_Data.xlsx"


def get_default_report_path(format: str = "csv") -> str:
    """
    Finds default output report path relative to package root for the requested format.

    Args:
        format: Target file format, either 'csv' or 'xlsx' (also accepts 'excel').
                Defaults to 'csv'.

    Returns:
        Path string to the default report file.

    Raises:
        ValueError: If an unsupported format is requested.
    """
    fmt_clean = str(format).strip().lower().lstrip(".")
    if fmt_clean == "csv":
        ext = "csv"
    elif fmt_clean in {"xlsx", "excel"}:
        ext = "xlsx"
    else:
        raise ValueError(
            f"Unsupported report format: '{format}'. Supported formats are 'csv' and 'xlsx'."
        )

    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_data = os.path.join(pkg_root, "data")
    filename = f"BharatTrip_Reconciliation_Report.{ext}"
    if os.path.exists(target_data):
        return os.path.join(target_data, filename)
    return os.path.join("data", filename)


class BharatTripReconciliationEngine:
    """
    In-memory deterministic reconciliation engine that indexes the workbook
    and provides reference-level reconciliation, duplicate detection, and batch audit.
    """

    def __init__(self, workbook_path: Optional[str] = None):
        self.workbook_path = workbook_path or get_default_workbook_path()
        self.df_support = pd.DataFrame()
        self.df_finance = pd.DataFrame()
        self.df_escalation = pd.DataFrame()

        # Indexes
        self.support_by_ref: Dict[str, List[Tuple[int, pd.Series]]] = {}
        self.finance_by_ref: Dict[str, List[Tuple[int, pd.Series]]] = {}
        self.escalation_by_ref: Dict[str, List[Tuple[int, pd.Series]]] = {}
        self.ambiguous_records: List[Dict[str, Any]] = []

        self.load_and_index()

    def load_and_index(self) -> None:
        """Loads and indexes sheets from the workbook strictly in read-only mode."""
        if not os.path.exists(self.workbook_path):
            raise FileNotFoundError(f"Workbook not found at '{self.workbook_path}'")

        sheets = pd.read_excel(self.workbook_path, sheet_name=None, engine="openpyxl")
        self.df_support = sheets.get("Support_Tracker", pd.DataFrame())
        self.df_finance = sheets.get("Finance_Tracker", pd.DataFrame())
        self.df_escalation = sheets.get("Escalation", sheets.get("Escalations", pd.DataFrame()))

        self._index_support()
        self._index_finance()
        self._index_escalations()

    def _index_support(self) -> None:
        self.support_by_ref.clear()
        for idx, row in self.df_support.iterrows():
            excel_row = idx + 2  # 1-based, header is row 1
            raw_id = row.get("Ticket ID")
            norm_res = normalize_reference(raw_id)

            if norm_res.is_ambiguous:
                self.ambiguous_records.append({
                    "sheet": "Support_Tracker",
                    "row": excel_row,
                    "raw": str(raw_id),
                    "candidates": norm_res.candidate_refs
                })
                continue

            if norm_res.is_valid and norm_res.normalized:
                self.support_by_ref.setdefault(norm_res.normalized, []).append((excel_row, row))

    def _index_finance(self) -> None:
        self.finance_by_ref.clear()
        for idx, row in self.df_finance.iterrows():
            excel_row = idx + 2
            raw_ref = row.get("Ref No")
            norm_res = normalize_reference(raw_ref)

            if norm_res.is_ambiguous:
                self.ambiguous_records.append({
                    "sheet": "Finance_Tracker",
                    "row": excel_row,
                    "raw": str(raw_ref),
                    "candidates": norm_res.candidate_refs
                })
                continue

            if norm_res.is_valid and norm_res.normalized:
                self.finance_by_ref.setdefault(norm_res.normalized, []).append((excel_row, row))

    def _index_escalations(self) -> None:
        self.escalation_by_ref.clear()
        for idx, row in self.df_escalation.iterrows():
            excel_row = idx + 2
            raw_ref = row.get("Related Ticket / Ref")
            norm_res = normalize_reference(raw_ref)

            if norm_res.is_valid and norm_res.normalized:
                self.escalation_by_ref.setdefault(norm_res.normalized, []).append((excel_row, row))

    def reconcile_reference(self, reference_query: str) -> ReconciliationItem:
        """
        Deterministically reconciles a single reference query against indexed data.
        Returns a rich ReconciliationItem object.
        """
        norm_res = normalize_reference(reference_query)

        if not norm_res.is_valid or not norm_res.normalized:
            if norm_res.is_ambiguous:
                return ReconciliationItem(
                    normalized_reference=reference_query,
                    original_references=[reference_query],
                    support_rows=[],
                    finance_rows=[],
                    escalation_rows=[],
                    support_status="UNKNOWN",
                    finance_payout_status="UNKNOWN",
                    escalation_status=None,
                    support_amount=None,
                    finance_amount_paid=None,
                    finance_deduction=None,
                    deduction_status="MISSING",
                    amount_difference=None,
                    is_deduction_explained=False,
                    primary_category="AMBIGUOUS_REFERENCE",
                    issue_flags=["AMBIGUOUS_REFERENCE"],
                    needs_human_review=True,
                    human_review_reasons=[f"Reference contains multiple potential tickets: {norm_res.candidate_refs}"],
                    explanation="Multiple refund references found in query. Please specify exactly one Ticket ID."
                )
            return ReconciliationItem(
                normalized_reference=reference_query or "MISSING",
                original_references=[reference_query or ""],
                support_rows=[],
                finance_rows=[],
                escalation_rows=[],
                support_status="UNKNOWN",
                finance_payout_status="UNKNOWN",
                escalation_status=None,
                support_amount=None,
                finance_amount_paid=None,
                finance_deduction=None,
                deduction_status="MISSING",
                amount_difference=None,
                is_deduction_explained=False,
                primary_category="INSUFFICIENT_DATA",
                issue_flags=["MISSING_REFERENCE"],
                needs_human_review=True,
                human_review_reasons=["Missing or empty refund reference"],
                explanation="No refund reference provided. A valid BharatTrip Ticket ID (e.g. RF-1286) is required."
            )

        ref = norm_res.normalized
        s_matches = self.support_by_ref.get(ref, [])
        f_matches = self.finance_by_ref.get(ref, [])
        e_matches = self.escalation_by_ref.get(ref, [])

        support_rows = [r[0] for r in s_matches]
        finance_rows = [r[0] for r in f_matches]
        escalation_rows = [r[0] for r in e_matches]

        orig_refs = list({str(r[1].get("Ticket ID")) for r in s_matches} |
                          {str(r[1].get("Ref No")) for r in f_matches} |
                          {reference_query})

        issue_flags: List[str] = []
        human_review_reasons: List[str] = []

        # Check escalations
        escalation_status = None
        escalation_id = None
        escalation_complaint = None
        if e_matches:
            esc_row = e_matches[0][1]
            escalation_status = clean_status(esc_row.get("Status"))
            escalation_id = str(esc_row.get("Escalation ID")) if pd.notna(esc_row.get("Escalation ID")) else None
            escalation_complaint = str(esc_row.get("Complaint")) if pd.notna(esc_row.get("Complaint")) else None
            issue_flags.append(f"ESCALATION_{escalation_status.upper()}")

        # ----------------------------------------------------------------------
        # Case A: Finance-Only Record (Found in Finance, Missing in Support)
        # ----------------------------------------------------------------------
        if not s_matches and f_matches:
            f_row = f_matches[0][1]
            f_status = clean_status(f_row.get("Payout Status"))
            f_paid = f_row.get("Amount Paid (INR)")
            f_ded = f_row.get("Deduction (INR)")
            paid_val = None if pd.isna(f_paid) else float(f_paid)

            if pd.isna(f_ded):
                ded_status = "MISSING"
                ded_val = None
            elif float(f_ded) == 0.0:
                ded_status = "EXPLICIT_ZERO"
                ded_val = 0.0
            else:
                ded_status = "RECORDED_POSITIVE"
                ded_val = float(f_ded)

            issue_flags.append("FINANCE_ONLY_RECORDS")
            human_review_reasons.append(
                f"Finance processed transaction (Status: {f_status}, Paid: ₹{paid_val}), "
                "but Support has zero ticket logged for this reference."
            )

            explanation = (
                f"Reference {ref} exists only in Finance records (Row {finance_rows[0]}), "
                f"with payout status '{f_status}' for ₹{paid_val}. Support never recorded a ticket."
            )

            return ReconciliationItem(
                normalized_reference=ref,
                original_references=orig_refs,
                support_rows=[],
                finance_rows=finance_rows,
                escalation_rows=escalation_rows,
                support_status="NOT_FOUND",
                finance_payout_status=f_status,
                escalation_status=escalation_status,
                support_amount=None,
                finance_amount_paid=paid_val,
                finance_deduction=ded_val,
                deduction_status=ded_status,
                amount_difference=None,
                is_deduction_explained=False,
                primary_category="FINANCE_ONLY_RECORDS",
                issue_flags=issue_flags,
                needs_human_review=True,
                human_review_reasons=human_review_reasons,
                explanation=explanation,
                agent_name=str(f_row.get("Agent Name")),
                route=str(f_row.get("Sector")),
                finance_remarks=str(f_row.get("Remarks")),
                approved_by=str(f_row.get("Approved By")) if pd.notna(f_row.get("Approved By")) else None,
                finance_received_on=normalize_date(f_row.get("Received On")),
                finance_processed_on=normalize_date(f_row.get("Processed On")),
                escalation_id=escalation_id,
                escalation_complaint=escalation_complaint,
            )

        # ----------------------------------------------------------------------
        # Case B: Missing in Finance (Found in Support, Missing in Finance)
        # ----------------------------------------------------------------------
        if s_matches and not f_matches:
            s_row = s_matches[0][1]
            s_status = clean_status(s_row.get("Status"))
            s_amt = s_row.get("Refund Amount (INR)")
            s_amt_val = None if pd.isna(s_amt) else float(s_amt)

            # Check if Support itself has duplicate/conflicting records
            if len(s_matches) > 1:
                amts = [r[1].get("Refund Amount (INR)") for r in s_matches]
                stats = [clean_status(r[1].get("Status")) for r in s_matches]
                if len(set(amts)) > 1 or len(set(stats)) > 1:
                    issue_flags.append("SUPPORT_DUPLICATE_REVIEW")
                    issue_flags.append("SUPPORT_CONFLICTING_DUPLICATE")
                    human_review_reasons.append(
                        f"Support has conflicting duplicates in rows {support_rows} (Amounts: {amts}, Statuses: {stats})."
                    )
                else:
                    issue_flags.append("SUPPORT_EXACT_DUPLICATE")

            issue_flags.append("MISSING_IN_FINANCE")
            human_review_reasons.append(
                f"Support logged ticket as '{s_status}' for ₹{s_amt_val}, "
                "but Finance has zero record of receiving or queuing this payout."
            )

            explanation = (
                f"Support recorded refund ticket {ref} (Status: '{s_status}', Claim: ₹{s_amt_val}), "
                "but Finance has NO record of this request. Payout was never credited."
            )

            return ReconciliationItem(
                normalized_reference=ref,
                original_references=orig_refs,
                support_rows=support_rows,
                finance_rows=[],
                escalation_rows=escalation_rows,
                support_status=s_status,
                finance_payout_status="NOT_FOUND",
                escalation_status=escalation_status,
                support_amount=s_amt_val,
                finance_amount_paid=None,
                finance_deduction=None,
                deduction_status="MISSING",
                amount_difference=s_amt_val,
                is_deduction_explained=False,
                primary_category="MISSING_IN_FINANCE",
                issue_flags=issue_flags,
                needs_human_review=True,
                human_review_reasons=human_review_reasons,
                explanation=explanation,
                agent_name=str(s_row.get("Agent")),
                route=str(s_row.get("Route")),
                support_notes=str(s_row.get("Notes")),
                escalation_id=escalation_id,
                escalation_complaint=escalation_complaint,
            )

        # ----------------------------------------------------------------------
        # Case C: Not found anywhere
        # ----------------------------------------------------------------------
        if not s_matches and not f_matches:
            return ReconciliationItem(
                normalized_reference=ref,
                original_references=orig_refs,
                support_rows=[],
                finance_rows=[],
                escalation_rows=escalation_rows,
                support_status="NOT_FOUND",
                finance_payout_status="NOT_FOUND",
                escalation_status=escalation_status,
                support_amount=None,
                finance_amount_paid=None,
                finance_deduction=None,
                deduction_status="MISSING",
                amount_difference=None,
                is_deduction_explained=False,
                primary_category="MISSING_IN_FINANCE",
                issue_flags=["NOT_FOUND_IN_SUPPORT_OR_FINANCE"],
                needs_human_review=True,
                human_review_reasons=[f"Reference {ref} not found in Support or Finance records."],
                explanation=f"Reference {ref} was not found in Support Tracker or Finance Tracker.",
                escalation_id=escalation_id,
                escalation_complaint=escalation_complaint,
            )

        # ----------------------------------------------------------------------
        # Case D: Both Support and Finance records exist
        # ----------------------------------------------------------------------

        # Inspect Support duplicates
        has_support_dups = len(s_matches) > 1
        support_amounts = [r[1].get("Refund Amount (INR)") for r in s_matches]
        support_statuses = [clean_status(r[1].get("Status")) for r in s_matches]

        is_conflicting_support = False
        has_support_amt_conflict = False
        has_support_stat_conflict = False

        if has_support_dups:
            unique_amts = {a for a in support_amounts if pd.notna(a)}
            unique_stats = set(support_statuses)
            has_support_amt_conflict = len(unique_amts) > 1
            has_support_stat_conflict = len(unique_stats) > 1

            if has_support_amt_conflict or has_support_stat_conflict:
                is_conflicting_support = True
                issue_flags.append("SUPPORT_DUPLICATE_REVIEW")
                issue_flags.append("SUPPORT_CONFLICTING_DUPLICATE")
                human_review_reasons.append(
                    f"Support has {len(s_matches)} conflicting records in rows {support_rows} "
                    f"(Amounts: {support_amounts}, Statuses: {support_statuses})."
                )
            else:
                issue_flags.append("SUPPORT_EXACT_DUPLICATE")

        # Inspect Finance duplicates
        has_finance_dups = len(f_matches) > 1
        if has_finance_dups:
            issue_flags.append("DUPLICATE_IN_FINANCE")
            fin_statuses = [clean_status(r[1].get("Payout Status")) for r in f_matches]
            fin_remarks = [str(r[1].get("Remarks")) for r in f_matches]
            human_review_reasons.append(
                f"Finance has {len(f_matches)} rows for this reference in rows {finance_rows} "
                f"(Statuses: {fin_statuses}, Remarks: {fin_remarks}). Risk of double payout."
            )

        # Primary operational series
        s_row = s_matches[0][1]
        f_row = f_matches[0][1]

        s_status = clean_status(s_row.get("Status"))
        f_status = clean_status(f_row.get("Payout Status"))

        s_amt = s_row.get("Refund Amount (INR)")
        s_amt_val = None if pd.isna(s_amt) else float(s_amt)

        f_paid = f_row.get("Amount Paid (INR)")
        f_paid_val = None if pd.isna(f_paid) else float(f_paid)

        # ----------------------------------------------------------------------
        # Deduction Field Analysis: Explicit Zero vs Positive vs Missing
        # ----------------------------------------------------------------------
        f_ded = f_row.get("Deduction (INR)")
        if pd.isna(f_ded):
            ded_status = "MISSING"
            ded_val = None
        elif float(f_ded) == 0.0:
            ded_status = "EXPLICIT_ZERO"
            ded_val = 0.0
        else:
            ded_status = "RECORDED_POSITIVE"
            ded_val = float(f_ded)

        # Flag missing approver if paid
        approved_by = f_row.get("Approved By")
        if pd.isna(approved_by) and f_status == "Refund Done":
            issue_flags.append("MISSING_FINANCE_APPROVER")

        # Amount comparison
        amount_diff = None
        is_deduction_explained = False

        if s_amt_val is not None and f_paid_val is not None:
            amount_diff = round(s_amt_val - f_paid_val, 2)
            if ded_status == "RECORDED_POSITIVE" and ded_val is not None:
                if abs(amount_diff - ded_val) < 0.01:
                    is_deduction_explained = True

        # Secondary flag: check if Support lag exists (Finance Paid but Support has Working)
        has_support_working = ("Working" in support_statuses or s_status == "Working")
        if f_status == "Refund Done" and has_support_working:
            issue_flags.append("PAID_BUT_SUPPORT_WORKING")
            issue_flags.append("SUPPORT_LAG")

        # ----------------------------------------------------------------------
        # Primary Category Determination & Explanations
        # ----------------------------------------------------------------------
        needs_human_review = False
        explanation = ""

        # Priority 1: Conflicting Support Duplicate Amounts
        if has_support_amt_conflict:
            primary_category = "SUPPORT_DUPLICATE_REVIEW"
            needs_human_review = True
            explanation = (
                f"Reference {ref} appears in multiple Support rows ({support_rows}) with contradictory "
                f"claim amounts ({support_amounts}). Human supervisor review required to confirm claim."
            )

        # Priority 2: Duplicate Finance Rows
        elif has_finance_dups:
            primary_category = "DUPLICATE_IN_FINANCE"
            needs_human_review = True
            explanation = (
                f"Reference {ref} appears in multiple Finance rows ({finance_rows}). "
                "Finance amounts cannot be summed; manual review required to verify single payout."
            )

        # Priority 3: Support Closed, but Finance Pending Payout
        elif s_status == "Closed" and f_status == "Pending Payout":
            primary_category = "CLOSED_BUT_PENDING"
            needs_human_review = True
            issue_flags.append("CLOSED_BUT_PENDING")
            human_review_reasons.append(
                f"Support marked ticket 'Closed', but Finance has payout pending ({f_row.get('Remarks')})."
            )
            explanation = (
                f"Support marked ticket {ref} as 'Closed', but Finance has NOT disbursed payment yet. "
                f"Finance Payout Status is 'Pending Payout' (Remarks: '{f_row.get('Remarks')}')."
            )

        # Priority 4: Support Closed, but Finance Declined
        elif s_status == "Closed" and f_status == "Declined":
            primary_category = "CLOSED_BUT_DECLINED"
            needs_human_review = True
            issue_flags.append("CLOSED_BUT_DECLINED")
            human_review_reasons.append(
                f"Support closed ticket, but Finance declined payment ({f_row.get('Remarks')})."
            )
            explanation = (
                f"Support closed ticket {ref}, but Finance explicitly DECLINED the refund "
                f"(Remarks: '{f_row.get('Remarks')}'). Money was never disbursed."
            )

        # Priority 5: Finance Refund Done, but Support Working (or has a Working duplicate)
        elif f_status == "Refund Done" and has_support_working:
            primary_category = "PAID_BUT_SUPPORT_WORKING"
            if is_conflicting_support:
                needs_human_review = True
                explanation = (
                    f"Finance completed payout of ₹{f_paid_val} for {ref} on {f_row.get('Processed On')}, "
                    f"but Support has contradictory duplicate rows ({support_rows}) including an open 'Working' ticket. Review required."
                )
            else:
                explanation = (
                    f"Finance completed payout of ₹{f_paid_val} for {ref} on {f_row.get('Processed On')}, "
                    "but Support still shows the ticket as 'Working'. Agent has not been updated."
                )

        # Priority 6: Conflicting Support Duplicate Statuses (where not captured above)
        elif is_conflicting_support:
            primary_category = "SUPPORT_DUPLICATE_REVIEW"
            needs_human_review = True
            explanation = (
                f"Reference {ref} appears in multiple Support rows ({support_rows}) with contradictory "
                f"statuses ({support_statuses}). Review required."
            )

        # Priority 7: Explained Deduction vs Amount Mismatch
        elif f_status == "Refund Done" and amount_diff is not None and abs(amount_diff) > 0.01:
            if is_deduction_explained:
                primary_category = "EXPLAINED_DEDUCTION"
                issue_flags.append("EXPLAINED_DEDUCTION")
                issue_flags.append("UNVERIFIED_POLICY_AUTHORIZATION")
                explanation = (
                    f"Support recorded refund request of ₹{s_amt_val}, but Finance paid ₹{f_paid_val}. "
                    f"The arithmetic difference of ₹{amount_diff} matches the recorded Finance deduction of ₹{ded_val} ({f_row.get('Remarks')}). "
                    "Note: Deduction authorization and policy compliance are unverified (no approved policy document available)."
                )
            else:
                primary_category = "AMOUNT_MISMATCH"
                needs_human_review = True

                if ded_status == "MISSING":
                    issue_flags.append("UNVERIFIED_AMOUNT_DIFFERENCE")
                    issue_flags.append("MISSING_DEDUCTION_FIELD")
                    human_review_reasons.append(
                        f"Support claimed ₹{s_amt_val}, Finance paid ₹{f_paid_val}. Finance Deduction is missing (blank). Variance is unverified."
                    )
                    explanation = (
                        f"Support claimed ₹{s_amt_val}, but Finance paid ₹{f_paid_val}. Finance Deduction is missing (blank). "
                        f"Variance of ₹{amount_diff} cannot be verified against any deduction figure. Human review required."
                    )
                elif ded_status == "EXPLICIT_ZERO":
                    issue_flags.append("UNEXPLAINED_AMOUNT_DIFFERENCE")
                    issue_flags.append("EXPLICIT_ZERO_DEDUCTION_MISMATCH")
                    human_review_reasons.append(
                        f"Support claimed ₹{s_amt_val}, Finance paid ₹{f_paid_val} with explicit zero deduction recorded."
                    )
                    explanation = (
                        f"Support claimed ₹{s_amt_val}, but Finance paid ₹{f_paid_val} with an explicit zero deduction recorded (₹0.0). "
                        f"The variance of ₹{amount_diff} is unexplained. Human review required."
                    )
                else:
                    issue_flags.append("UNEXPLAINED_AMOUNT_DIFFERENCE")
                    remaining = round(amount_diff - (ded_val or 0.0), 2)
                    human_review_reasons.append(
                        f"Amount difference of ₹{amount_diff} is not fully accounted for by deduction (₹{ded_val}). Remaining variance: ₹{remaining}."
                    )
                    explanation = (
                        f"Support claimed ₹{s_amt_val}, but Finance paid ₹{f_paid_val} with deduction ₹{ded_val}. "
                        f"The remaining variance of ₹{remaining} is unexplained. Human review required."
                    )

        # Priority 8: Clean Match (Both agree, full refund settled)
        elif f_status == "Refund Done" and s_status == "Closed" and (amount_diff is None or abs(amount_diff) <= 0.01):
            primary_category = "CLEAN_MATCH_REFUNDED"
            explanation = (
                f"Refund fully reconciled and settled. Support claimed ₹{s_amt_val} and Finance paid ₹{f_paid_val} "
                f"via {f_row.get('Remarks')} on {f_row.get('Processed On')}."
            )

        # Priority 9: Consistent In-Progress or Declined
        else:
            primary_category = "IN_PROGRESS_OR_DECLINED_CONSISTENT"
            if s_status == "Not Eligible" and f_status == "Declined":
                explanation = (
                    f"Support marked request as 'Not Eligible' and Finance declined the payout "
                    f"(Remarks: '{f_row.get('Remarks')}'). Records are consistent."
                )
            elif f_status == "Pending Payout":
                needs_human_review = True
                explanation = (
                    f"Refund request is currently pending in Finance ({f_row.get('Remarks')}). "
                    f"Support status: '{s_status}'."
                )
            else:
                needs_human_review = True
                explanation = (
                    f"Refund status: Support='{s_status}', Finance='{f_status}' ({f_row.get('Remarks')}). "
                    "Review required."
                )

        if human_review_reasons:
            needs_human_review = True

        return ReconciliationItem(
            normalized_reference=ref,
            original_references=orig_refs,
            support_rows=support_rows,
            finance_rows=finance_rows,
            escalation_rows=escalation_rows,
            support_status=s_status,
            finance_payout_status=f_status,
            escalation_status=escalation_status,
            support_amount=s_amt_val,
            finance_amount_paid=f_paid_val,
            finance_deduction=ded_val,
            deduction_status=ded_status,
            amount_difference=amount_diff,
            is_deduction_explained=is_deduction_explained,
            primary_category=primary_category,
            issue_flags=issue_flags,
            needs_human_review=needs_human_review,
            human_review_reasons=human_review_reasons,
            explanation=explanation,
            agent_name=str(s_row.get("Agent") or f_row.get("Agent Name")),
            route=str(s_row.get("Route") or f_row.get("Sector")),
            finance_remarks=str(f_row.get("Remarks")),
            support_notes=str(s_row.get("Notes")),
            approved_by=str(f_row.get("Approved By")) if pd.notna(f_row.get("Approved By")) else None,
            finance_received_on=normalize_date(f_row.get("Received On")),
            finance_processed_on=normalize_date(f_row.get("Processed On")),
            escalation_id=escalation_id,
            escalation_complaint=escalation_complaint,
        )

    def run_full_reconciliation(self) -> List[ReconciliationItem]:
        """Runs comprehensive reconciliation across all indexed references."""
        all_refs = set(self.support_by_ref.keys()) | set(self.finance_by_ref.keys())
        results = [self.reconcile_reference(ref) for ref in sorted(all_refs)]
        return results

    def generate_reconciliation_report(self, output_path: Optional[str] = None, export_excel: bool = True) -> pd.DataFrame:
        """Generates and writes reconciliation reports without modifying the original Excel workbook."""
        target_path = output_path or get_default_report_path(format="csv")
        results = self.run_full_reconciliation()
        report_data = [item.to_dict() for item in results]
        df_report = pd.DataFrame(report_data)

        parent_dir = os.path.dirname(os.path.abspath(target_path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        df_report.to_csv(target_path, index=False, encoding="utf-8")

        if export_excel:
            excel_path = target_path.rsplit(".", 1)[0] + ".xlsx" if "." in target_path else target_path + ".xlsx"
            excel_dir = os.path.dirname(os.path.abspath(excel_path))
            if excel_dir:
                os.makedirs(excel_dir, exist_ok=True)
            df_report.to_excel(excel_path, index=False, engine="openpyxl")
        return df_report


if __name__ == "__main__":
    engine = BharatTripReconciliationEngine()
    print("BharatTrip Reconciliation Engine loaded successfully.")
    print(f"Indexed Support unique references: {len(engine.support_by_ref)}")
    print(f"Indexed Finance unique references: {len(engine.finance_by_ref)}")
    print(f"Ambiguous records found: {len(engine.ambiguous_records)}")

    report_df = engine.generate_reconciliation_report()
    print(f"\nReconciliation Report generated: {len(report_df)} total unique reconciled references.")
    print("\nBreakdown by Primary Category:")
    print(report_df["Primary Category"].value_counts())
    print("\nRecords Needing Human Review:")
    print(report_df["Needs Human Review"].value_counts())
