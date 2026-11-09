"""
tests/test_streamlit_bharattrip_refund.py
-----------------------------------------
Automated verification tests for Phase 4 Streamlit frontend logic and Section 7 End-to-End Tests:
  TEST A: RF-1252 (Explained deduction: ₹21,100 Support claim, ₹19,400 Finance paid, ₹1,700 deduction, unverified policy)
  TEST B: RF-1570 (Missing in Finance: Human review required, pause and resume)
  TEST C: RF-1148 (Duplicate Finance records: Human review required)
  TEST D: RF-1757 (Closed but pending: Human review required)
  TEST E: RF-1286 (Clean match with correct Support and Finance evidence)
  TEST F: Missing reference (Ask employee for valid refund reference)
  TEST G: Ambiguous reference (Identify multiple candidates and request clarification)
  TEST H: Refund execution request (Refuse autonomous execution or approval)
  TEST I: Invalid / Nonexistent reference (Explain no matching record found without fabricating)
  TEST J: New investigation isolation (Verify state and thread isolation without leakage)
  Non-regression: Verify original chatbot modules continue to function
"""

import os
import sys
import unittest
import uuid

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from bharattrip_refund_backend import (
    run_refund_investigation,
    resume_with_human_decision,
)
from streamlit_bharattrip_refund import SAMPLE_COMPLAINTS, GOVERNANCE_DISCLAIMER


class TestStreamlitBharatTripRefundFrontend(unittest.TestCase):
    """End-to-End test suite matching Section 7 requirements (TEST A through TEST J)."""

    def test_sample_complaints_configured(self):
        """Verifies that preset test complaints and governance disclaimer are properly configured."""
        self.assertTrue(any("RF-1252" in k for k in SAMPLE_COMPLAINTS))
        self.assertTrue(any("RF-1570" in k for k in SAMPLE_COMPLAINTS))
        self.assertTrue(any("RF-1148" in k for k in SAMPLE_COMPLAINTS))
        self.assertTrue(any("RF-1757" in k for k in SAMPLE_COMPLAINTS))
        self.assertTrue(any("Missing Reference" in k for k in SAMPLE_COMPLAINTS))
        self.assertTrue(any("Ambiguous Reference" in k for k in SAMPLE_COMPLAINTS))
        self.assertIn("All refund approvals and financial transactions require authorized human action", GOVERNANCE_DISCLAIMER)

    # TEST A: RF-1252 (Explained deduction)
    def test_a_rf_1252_explained_deduction(self):
        thread_id = f"test-a-1252-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "RF-1252" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertEqual(result.get("refund_reference"), "RF-1252")
        recon = result.get("reconciliation_result") or {}
        self.assertEqual(recon.get("Primary Category"), "EXPLAINED_DEDUCTION")
        self.assertEqual(recon.get("Support Amount (INR)"), 21100.0)
        self.assertEqual(recon.get("Finance Amount Paid (INR)"), 19400.0)
        self.assertEqual(recon.get("Finance Deduction (INR)"), 1700.0)
        self.assertIn("UNVERIFIED_POLICY_AUTHORIZATION", result.get("issue_flags", []))
        self.assertFalse(result.get("human_review_required"))

        report = result.get("final_report", "")
        self.assertIn("21100", report)
        self.assertIn("19400", report)
        self.assertIn("1700", report)
        self.assertIn("unverified", report.lower())

    # TEST B: RF-1570 (Missing in Finance: Human review pause and resume)
    def test_b_rf_1570_missing_in_finance(self):
        thread_id = f"test-b-1570-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "RF-1570" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertEqual(result.get("refund_reference"), "RF-1570")
        recon = result.get("reconciliation_result") or {}
        self.assertEqual(recon.get("Primary Category"), "MISSING_IN_FINANCE")
        self.assertTrue(result.get("human_review_required"))
        self.assertIn("__interrupt__", result)

        # Resume the paused thread
        resumed = resume_with_human_decision("SUBMIT_TICKET_TO_FINANCE", thread_id=thread_id)
        self.assertEqual(resumed.get("human_decision"), "SUBMIT_TICKET_TO_FINANCE")
        self.assertEqual(resumed.get("investigation_status"), "COMPLETED")
        self.assertIn("SUBMIT_TICKET_TO_FINANCE", resumed.get("final_report", ""))

    # TEST C: RF-1148 (Duplicate Finance records)
    def test_c_rf_1148_duplicate_finance(self):
        thread_id = f"test-c-1148-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "RF-1148" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertEqual(result.get("refund_reference"), "RF-1148")
        recon = result.get("reconciliation_result") or {}
        self.assertEqual(recon.get("Primary Category"), "DUPLICATE_IN_FINANCE")
        self.assertTrue(result.get("human_review_required"))
        self.assertIn("DUPLICATE_IN_FINANCE", result.get("issue_flags", []))

    # TEST D: RF-1757 (Closed but pending)
    def test_d_rf_1757_closed_but_pending(self):
        thread_id = f"test-d-1757-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "RF-1757" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertEqual(result.get("refund_reference"), "RF-1757")
        recon = result.get("reconciliation_result") or {}
        self.assertEqual(recon.get("Primary Category"), "CLOSED_BUT_PENDING")
        self.assertTrue(result.get("human_review_required"))

    # TEST E: RF-1286 (Clean match settled refund)
    def test_e_rf_1286_clean_match(self):
        thread_id = f"test-e-1286-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "RF-1286" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertEqual(result.get("refund_reference"), "RF-1286")
        recon = result.get("reconciliation_result") or {}
        self.assertEqual(recon.get("Primary Category"), "CLEAN_MATCH_REFUNDED")
        self.assertEqual(recon.get("Support Amount (INR)"), 10200.0)
        self.assertEqual(recon.get("Finance Amount Paid (INR)"), 10200.0)
        self.assertFalse(result.get("human_review_required"))

    # TEST F: Missing reference (Asks for valid refund reference)
    def test_f_missing_reference(self):
        thread_id = f"test-f-missing-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "Missing Reference" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertIn("refund_reference", result.get("missing_fields", []))
        self.assertIn("Insufficient Information", result.get("final_report", ""))
        self.assertIn("Please provide a valid BharatTrip Ticket ID", result.get("final_report", ""))

    # TEST G: Ambiguous reference (Identifies candidates and requests clarification)
    def test_g_ambiguous_reference(self):
        thread_id = f"test-g-ambig-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "Ambiguous Reference" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertIn("ambiguous_reference", result.get("missing_fields", []))
        self.assertIn("Ambiguous Reference", result.get("final_report", ""))
        self.assertIn("RF-1001", str(result.get("extracted_information", {}).get("candidate_refs", [])))

    # TEST H: Refund execution attempt (Blocked autonomous action)
    def test_h_refund_execution_attempt_blocked(self):
        thread_id = f"test-h-exec-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "Execution Attempt" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        self.assertTrue(result.get("human_review_required"))
        self.assertIn("EXECUTION_REQUEST_BLOCKED", result.get("issue_flags", []))
        self.assertEqual(result.get("classification", {}).get("category"), "EXECUTION_REQUEST")

    # TEST I: Invalid reference (Explains no matching record found, never fabricates)
    def test_i_invalid_nonexistent_reference(self):
        thread_id = f"test-i-invalid-{uuid.uuid4()}"
        complaint = [v for k, v in SAMPLE_COMPLAINTS.items() if "Invalid / Nonexistent" in k][0]

        result = run_refund_investigation(complaint, thread_id=thread_id)
        recon = result.get("reconciliation_result") or {}
        # Must report not found and require supervisor review
        self.assertEqual(recon.get("Support Status"), "NOT_FOUND")
        self.assertEqual(recon.get("Finance Payout Status"), "NOT_FOUND")
        self.assertIn("NOT_FOUND_IN_SUPPORT_OR_FINANCE", result.get("issue_flags", []))
        self.assertTrue(result.get("human_review_required"))

        # Resume to verify final report states record was not found
        resumed = resume_with_human_decision("SUBMIT_TICKET_TO_FINANCE", thread_id=thread_id)
        self.assertIn("RF-999999", resumed.get("final_report", ""))
        self.assertIn("not found in Support Tracker or Finance Tracker", resumed.get("final_report", ""))

    # TEST J: New investigation isolation (No state or thread leakage)
    def test_j_investigation_isolation(self):
        thread_1 = f"thread-iso-1-{uuid.uuid4()}"
        thread_2 = f"thread-iso-2-{uuid.uuid4()}"

        res1 = run_refund_investigation("Inquiry for RF-1252", thread_id=thread_1)
        res2 = run_refund_investigation("Inquiry for RF-1570", thread_id=thread_2)

        self.assertEqual(res1.get("refund_reference"), "RF-1252")
        self.assertEqual(res2.get("refund_reference"), "RF-1570")
        self.assertNotEqual(res1.get("reconciliation_result"), res2.get("reconciliation_result"))

    # Verify that all required BharatTrip modules are self-contained in src/
    def test_submission_self_contained(self):
        import bharattrip_reconciliation
        import bharattrip_refund_backend
        import bharattrip_history
        import streamlit_bharattrip_refund
        self.assertTrue(hasattr(bharattrip_reconciliation, "BharatTripReconciliationEngine"))
        self.assertTrue(hasattr(bharattrip_refund_backend, "create_refund_workflow"))
        self.assertTrue(hasattr(bharattrip_history, "init_history_db"))
        self.assertTrue(hasattr(streamlit_bharattrip_refund, "run_refund_investigation"))


if __name__ == "__main__":
    unittest.main()
