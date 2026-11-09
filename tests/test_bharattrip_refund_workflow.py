"""
tests/test_bharattrip_refund_workflow.py
----------------------------------------
Automated test suite for the dedicated BharatTrip Refund Operations LangGraph workflow.
Tests all nodes, conditional routing, reference validation, human-in-the-loop
interruptions, persistence using an actual SQLite database file, resumption via
Command(resume=...), standardized decision strings ('SUBMIT_TICKET_TO_FINANCE'),
audit-only supervisor recording, explicit escalation record reporting,
and exact operational recommendations across all reconciliation categories.
"""

import os
import sys
import unittest
import sqlite3

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from bharattrip_refund_backend import (
    create_refund_workflow,
    run_refund_investigation,
    resume_with_human_decision,
    extract_info_with_fallback,
)
from bharattrip_reconciliation import BharatTripReconciliationEngine

TEST_DB_PATH = "test_refund_checkpoint.db"
STANDARDIZED_DECISION = "SUBMIT_TICKET_TO_FINANCE"


class MockLLM:
    """Mock LLM to ensure deterministic, fast, offline unit testing."""
    def invoke(self, messages):
        class MockResp:
            content = "{}"
        return MockResp()


class TestBharatTripRefundWorkflow(unittest.TestCase):
    """Integration test suite for the Phase 3 LangGraph workflow."""

    @classmethod
    def setUpClass(cls):
        # Remove any pre-existing test checkpoint database
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

        # Use an in-memory reconciliation engine and mock LLM for speed and determinism
        cls.engine = BharatTripReconciliationEngine()
        cls.mock_llm = MockLLM()

        # Create a test workflow with actual on-disk SQLite checkpointer
        cls.app = create_refund_workflow(
            reconciliation_engine=cls.engine,
            llm=cls.mock_llm,
            db_path=TEST_DB_PATH
        )

    @classmethod
    def tearDownClass(cls):
        # Clean up the test database file
        import gc
        if hasattr(cls, "app"):
            del cls.app
        gc.collect()
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # 1. Missing & Ambiguous References
    # --------------------------------------------------------------------------
    def test_missing_refund_reference(self):
        thread_id = "test-missing-ref"
        complaint = "Hello, an agent is asking for an update on their refund, but didn't provide a reference."
        res = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)

        self.assertIn("refund_reference", res["missing_fields"])
        self.assertIn("Insufficient Information", res["final_report"])
        self.assertFalse(res["human_review_required"])
        self.assertEqual(res["investigation_status"], "COMPLETED")

    def test_ambiguous_refund_reference(self):
        thread_id = "test-ambiguous-ref"
        complaint = "Agent is asking whether RF-1001 or RF-1002 was paid out."
        res = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)

        self.assertIn("ambiguous_reference", res["missing_fields"])
        self.assertIn("Ambiguous Reference", res["final_report"])
        self.assertEqual(res["investigation_status"], "COMPLETED")

    # --------------------------------------------------------------------------
    # 2. SQLite Persistence and Interrupt / Resume Verification
    # --------------------------------------------------------------------------
    def test_sqlite_persistence_and_resume_with_standard_decision(self):
        """Verifies checkpoint persistence on actual disk SQLite DB and standardized decision resume."""
        thread_id = "test-sqlite-persistence-thread"
        complaint = "Travel agent Skyline Travels is following up on ticket RF-1570 for BLR-DXB route."

        # Step 1: Execute workflow until human review interrupt
        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertTrue(state_1.get("human_review_required"))
        self.assertIn("__interrupt__", state_1)

        # Step 2: Verify the SQLite database file exists on disk and has checkpoint data
        self.assertTrue(os.path.exists(TEST_DB_PATH))
        conn = sqlite3.connect(TEST_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()
        self.assertTrue(len(tables) > 0, "SQLite checkpointer must create checkpoint tables.")

        # Step 3: Resume execution using standardized decision string SUBMIT_TICKET_TO_FINANCE
        state_2 = resume_with_human_decision(STANDARDIZED_DECISION, thread_id=thread_id, app=self.app)
        self.assertEqual(state_2["human_decision"], STANDARDIZED_DECISION)
        self.assertEqual(state_2["investigation_status"], "COMPLETED")

        # Step 4: Verify supervisor action is recorded strictly as an audit note
        final_report = state_2["final_report"]
        self.assertIn(f"- **Supervisor Action Note**: `{STANDARDIZED_DECISION}`", final_report)
        self.assertIn("Recorded strictly as an internal operational audit note", final_report)
        self.assertIn("This assistant does not approve, decline, or execute refunds.", final_report)
        self.assertIn("[GOVERNANCE POLICY & COMPLIANCE]", final_report)

        # Step 5: Verify missing escalation record is explicitly reported
        self.assertIn("- **Linked Escalation Record**: None (No linked escalation record found)", final_report)

        # Step 6: Verify recommendation for MISSING_IN_FINANCE
        self.assertIn(
            "Submit ticket immediately to Finance for intake. Inform agent that payout was delayed in Support-to-Finance handover.",
            final_report
        )

    # --------------------------------------------------------------------------
    # 3. Benchmark Case: RF-1286 (Clean Match Refunded)
    # --------------------------------------------------------------------------
    def test_rf_1286_clean_match_recommendation(self):
        thread_id = "test-rf-1286"
        complaint = "Status check for Wander Agency ticket RF-1286."

        res = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertFalse(res.get("human_review_required"))
        self.assertEqual(res["reconciliation_result"]["Primary Category"], "CLEAN_MATCH_REFUNDED")
        final_report = res["final_report"]
        self.assertIn(
            "No action required. Payout has already been settled in full as per Finance records.",
            final_report
        )
        self.assertIn("- **Linked Escalation Record**: None (No linked escalation record found)", final_report)
        self.assertIn("(Excel Source Row: `2`)", final_report)
        self.assertIn("(Excel Source Row: `509`)", final_report)

    # --------------------------------------------------------------------------
    # 4. Benchmark Case: RF-1252 (Explained Deduction)
    # --------------------------------------------------------------------------
    def test_rf_1252_explained_deduction_recommendation(self):
        thread_id = "test-rf-1252"
        complaint = "TripHub is querying why only partial refund was received for RF-1252."

        res = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertFalse(res.get("human_review_required"))
        self.assertEqual(res["reconciliation_result"]["Primary Category"], "EXPLAINED_DEDUCTION")
        self.assertEqual(res["reconciliation_result"]["Finance Deduction (INR)"], 1700.0)
        final_report = res["final_report"]
        self.assertIn(
            "Communicate the specific fee deduction to the agent. Note: Supporting policy authorization remains unverified.",
            final_report
        )
        self.assertIn("EXPLAINED_DEDUCTION", final_report)
        self.assertIn("1700", final_report)

    # --------------------------------------------------------------------------
    # 5. Benchmark Case: RF-1757 (Closed but Pending) -> Interrupt & Resume
    # --------------------------------------------------------------------------
    def test_rf_1757_closed_but_pending(self):
        thread_id = "test-rf-1757"
        complaint = "Wander Agency ticket RF-1757 shows closed on portal, but agent says money not received."

        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertTrue(state_1.get("human_review_required"))
        self.assertEqual(state_1["reconciliation_result"]["Primary Category"], "CLOSED_BUT_PENDING")
        self.assertIn("__interrupt__", state_1)

        # Resume with standardized decision
        state_2 = resume_with_human_decision(STANDARDIZED_DECISION, thread_id=thread_id, app=self.app)
        self.assertEqual(state_2["investigation_status"], "COMPLETED")
        final_report = state_2["final_report"]
        self.assertIn(
            "Expedite payment release with Finance team. Do not re-credit or close ticket again.",
            final_report
        )
        self.assertIn(f"- **Supervisor Action Note**: `{STANDARDIZED_DECISION}`", final_report)

    # --------------------------------------------------------------------------
    # 6. Benchmark Case: RF-1527 (Closed but Declined) -> Interrupt & Resume
    # --------------------------------------------------------------------------
    def test_rf_1527_closed_but_declined(self):
        thread_id = "test-rf-1527"
        complaint = "Checking refund status for Wander Agency ticket RF-1527."

        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertTrue(state_1.get("human_review_required"))
        self.assertEqual(state_1["reconciliation_result"]["Primary Category"], "CLOSED_BUT_DECLINED")

        state_2 = resume_with_human_decision(STANDARDIZED_DECISION, thread_id=thread_id, app=self.app)
        final_report = state_2["final_report"]
        self.assertIn(
            "Escalate to Operations Manager to verify whether decline reason applies, and update agent honestly regarding decline.",
            final_report
        )
        self.assertIn(f"- **Supervisor Action Note**: `{STANDARDIZED_DECISION}`", final_report)

    # --------------------------------------------------------------------------
    # 7. Benchmark Case: RF-1148 (Duplicate in Finance) -> Interrupt & Resume
    # --------------------------------------------------------------------------
    def test_rf_1148_duplicate_finance(self):
        thread_id = "test-rf-1148"
        complaint = "Skyline Travels RF-1148 refund status check."

        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertTrue(state_1.get("human_review_required"))
        self.assertEqual(state_1["reconciliation_result"]["Primary Category"], "DUPLICATE_IN_FINANCE")
        self.assertIn("DUPLICATE_IN_FINANCE", state_1["issue_flags"])

        state_2 = resume_with_human_decision(STANDARDIZED_DECISION, thread_id=thread_id, app=self.app)
        final_report = state_2["final_report"]
        self.assertIn(
            "Stop all processing immediately. Finance supervisor must audit both Finance rows to eliminate double-disbursement.",
            final_report
        )
        self.assertIn(f"- **Supervisor Action Note**: `{STANDARDIZED_DECISION}`", final_report)

    # --------------------------------------------------------------------------
    # 8. Benchmark Case: RF-1181 (Conflicting Support Duplicates) -> Interrupt & Resume
    # --------------------------------------------------------------------------
    def test_rf_1181_conflicting_support_duplicates(self):
        thread_id = "test-rf-1181"
        complaint = "Pinnacle Getaways RF-1181 inquiry."

        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertTrue(state_1.get("human_review_required"))
        self.assertEqual(state_1["reconciliation_result"]["Primary Category"], "SUPPORT_DUPLICATE_REVIEW")
        self.assertIn("SUPPORT_CONFLICTING_DUPLICATE", state_1["issue_flags"])

        state_2 = resume_with_human_decision(STANDARDIZED_DECISION, thread_id=thread_id, app=self.app)
        final_report = state_2["final_report"]
        self.assertIn(
            "Reconcile duplicate Support tickets to confirm correct claim amount before taking action.",
            final_report
        )
        self.assertIn(f"- **Supervisor Action Note**: `{STANDARDIZED_DECISION}`", final_report)

    # --------------------------------------------------------------------------
    # 9. Benchmark Case: RF-1232 (Multiple Issue Flags Preserved) -> Interrupt & Resume
    # --------------------------------------------------------------------------
    def test_rf_1232_multiple_issue_flags(self):
        thread_id = "test-rf-1232"
        complaint = "Status inquiry for GoFly Holidays RF-1232."

        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertEqual(state_1["reconciliation_result"]["Primary Category"], "PAID_BUT_SUPPORT_WORKING")
        self.assertIn("SUPPORT_DUPLICATE_REVIEW", state_1["issue_flags"])
        self.assertIn("PAID_BUT_SUPPORT_WORKING", state_1["issue_flags"])
        self.assertIn("SUPPORT_CONFLICTING_DUPLICATE", state_1["issue_flags"])

        state_2 = resume_with_human_decision(STANDARDIZED_DECISION, thread_id=thread_id, app=self.app)
        final_report = state_2["final_report"]
        self.assertIn(
            "Update Support ticket to 'Closed' and notify agent that money was already transferred.",
            final_report
        )

    # --------------------------------------------------------------------------
    # 10. Benchmark Case: RF-1110 (Finance-Only Record) -> Interrupt & Resume
    # --------------------------------------------------------------------------
    def test_rf_1110_finance_only_record(self):
        thread_id = "test-rf-1110"
        complaint = "Checking ticket RF-1110 for Lotus Travel Co."

        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertTrue(state_1.get("human_review_required"))
        self.assertEqual(state_1["reconciliation_result"]["Primary Category"], "FINANCE_ONLY_RECORDS")
        self.assertIn("FINANCE_ONLY_RECORDS", state_1["issue_flags"])

        state_2 = resume_with_human_decision(STANDARDIZED_DECISION, thread_id=thread_id, app=self.app)
        final_report = state_2["final_report"]
        self.assertIn(
            "Create back-dated Support tracking ticket for Finance transaction audit record.",
            final_report
        )
        self.assertIn("(Excel Source Row: None (Missing in Support))", final_report)

    # --------------------------------------------------------------------------
    # 11. Linked Escalation Record Details Reporting (RF-1325)
    # --------------------------------------------------------------------------
    def test_rf_1325_linked_escalation_details_reported(self):
        thread_id = "test-rf-1325-escalation"
        complaint = "Checking refund status for GoFly Holidays ticket RF-1325."

        res = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        final_report = res["final_report"]
        # Verify linked escalation record is reported with exact row and status
        self.assertIn("- **Linked Escalation Record**: Row(s) `2`", final_report)
        self.assertIn("Status: `Resolved`", final_report)
        self.assertIn("ID: `ESC-501`", final_report)

    # --------------------------------------------------------------------------
    # 12. Execution Request Blocked
    # --------------------------------------------------------------------------
    def test_execution_request_blocked(self):
        thread_id = "test-exec-block"
        complaint = "Please approve and execute the refund now for RF-1286."

        state_1 = run_refund_investigation(complaint, thread_id=thread_id, app=self.app)
        self.assertTrue(state_1.get("human_review_required"))
        self.assertIn("EXECUTION_REQUEST_BLOCKED", state_1["issue_flags"])
        self.assertEqual(state_1["classification"]["category"], "EXECUTION_REQUEST")


if __name__ == "__main__":
    unittest.main()
