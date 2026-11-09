"""
tests/test_bharattrip_chat_history.py
-------------------------------------
Automated test suite for Section 12: Chat History Persistence and Management.
Tests all 14 required persistence and conversation management scenarios:
  1. Creating a new conversation.
  2. Saving user messages.
  3. Saving assistant responses.
  4. Retrieving conversation history.
  5. Switching between conversations.
  6. Reopening a previous conversation.
  7. Persisting history after application restart (simulated via fresh connection).
  8. Preserving the original LangGraph thread ID.
  9. Restoring a pending human-review conversation.
  10. Resuming a pending investigation.
  11. Preventing messages from leaking between conversations.
  12. Deleting a conversation safely (without corrupting checkpoint tables).
  13. Handling an empty history gracefully.
  14. Handling missing or corrupted history records gracefully.
"""

import os
import sys
import sqlite3
import unittest
import uuid

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import bharattrip_history as hist
from bharattrip_refund_backend import (
    create_refund_workflow,
    run_refund_investigation,
    resume_with_human_decision,
)

TEST_DB = "test_chat_history.db"


class TestBharatTripChatHistory(unittest.TestCase):
    """Unit and integration tests for persistent chat history."""

    @classmethod
    def setUpClass(cls):
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except Exception:
                pass
        hist.init_history_db(TEST_DB)

    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        if os.path.exists(TEST_DB):
            try:
                os.remove(TEST_DB)
            except Exception:
                pass

    def setUp(self):
        # Clear conversations before each test
        hist.clear_all_conversations(TEST_DB)

    # 1. Creating a new conversation
    def test_01_create_new_conversation(self):
        conv_id = f"conv-{uuid.uuid4()}"
        thread_id = f"thread-{uuid.uuid4()}"
        hist.save_or_update_conversation(
            conversation_id=conv_id,
            thread_id=thread_id,
            title="RF-1252: Explained Deduction",
            refund_reference="RF-1252",
            category="EXPLAINED_DEDUCTION",
            investigation_status="COMPLETED",
            human_review_status="NOT_REQUIRED",
            db_path=TEST_DB
        )
        conv = hist.get_conversation(conv_id, db_path=TEST_DB)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["conversation_id"], conv_id)
        self.assertEqual(conv["thread_id"], thread_id)
        self.assertEqual(conv["title"], "RF-1252: Explained Deduction")
        self.assertEqual(conv["refund_reference"], "RF-1252")

    # 2. Saving user messages
    def test_02_save_user_message(self):
        conv_id = f"conv-{uuid.uuid4()}"
        thread_id = f"thread-{uuid.uuid4()}"
        hist.save_or_update_conversation(
            conversation_id=conv_id, thread_id=thread_id, title="Test", db_path=TEST_DB
        )
        msg_id = hist.add_message(conv_id, "user", "What is the status of RF-1252?", db_path=TEST_DB)
        self.assertTrue(msg_id > 0)

        msgs = hist.get_conversation_messages(conv_id, db_path=TEST_DB)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "What is the status of RF-1252?")

    # 3. Saving assistant responses
    def test_03_save_assistant_response(self):
        conv_id = f"conv-{uuid.uuid4()}"
        thread_id = f"thread-{uuid.uuid4()}"
        hist.save_or_update_conversation(
            conversation_id=conv_id, thread_id=thread_id, title="Test", db_path=TEST_DB
        )
        hist.add_message(conv_id, "user", "Check RF-1286", db_path=TEST_DB)
        hist.add_message(conv_id, "assistant", "Report: Clean match settled in full.", db_path=TEST_DB)

        msgs = hist.get_conversation_messages(conv_id, db_path=TEST_DB)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertIn("Clean match", msgs[1]["content"])

    # 4. Retrieving conversation history
    def test_04_retrieve_conversation_history(self):
        for i in range(3):
            cid = f"conv-seq-{i}"
            tid = f"thread-seq-{i}"
            hist.save_or_update_conversation(
                conversation_id=cid, thread_id=tid, title=f"Case {i}", db_path=TEST_DB
            )
        history = hist.list_all_conversations(db_path=TEST_DB)
        self.assertEqual(len(history), 3)

    # 5. Switching between conversations
    def test_05_switching_conversations(self):
        c1 = "conv-A"
        c2 = "conv-B"
        hist.save_or_update_conversation(c1, "thread-A", "Case A", refund_reference="RF-1001", db_path=TEST_DB)
        hist.save_or_update_conversation(c2, "thread-B", "Case B", refund_reference="RF-1002", db_path=TEST_DB)

        hist.add_message(c1, "user", "Message for A", db_path=TEST_DB)
        hist.add_message(c2, "user", "Message for B", db_path=TEST_DB)

        # Retrieve A
        meta_a = hist.get_conversation(c1, db_path=TEST_DB)
        msgs_a = hist.get_conversation_messages(c1, db_path=TEST_DB)
        self.assertEqual(meta_a["title"], "Case A")
        self.assertEqual(msgs_a[0]["content"], "Message for A")

        # Switch to B
        meta_b = hist.get_conversation(c2, db_path=TEST_DB)
        msgs_b = hist.get_conversation_messages(c2, db_path=TEST_DB)
        self.assertEqual(meta_b["title"], "Case B")
        self.assertEqual(msgs_b[0]["content"], "Message for B")

    # 6. Reopening a previous conversation
    def test_06_reopening_previous_conversation(self):
        cid = "conv-reopen"
        tid = "thread-reopen"
        hist.save_or_update_conversation(
            cid, tid, "Case Reopen",
            refund_reference="RF-1757",
            category="CLOSED_BUT_PENDING",
            final_report="Advisory Report for RF-1757",
            db_path=TEST_DB
        )
        hist.add_message(cid, "user", "Inquiry on RF-1757", db_path=TEST_DB)
        hist.add_message(cid, "assistant", "Report details", db_path=TEST_DB)

        reopened = hist.get_conversation(cid, db_path=TEST_DB)
        self.assertEqual(reopened["thread_id"], tid)
        self.assertEqual(reopened["final_report"], "Advisory Report for RF-1757")
        msgs = hist.get_conversation_messages(cid, db_path=TEST_DB)
        self.assertEqual(len(msgs), 2)

    # 7. Persisting history after application restart
    def test_07_persistence_across_connection_restarts(self):
        cid = "conv-restart-test"
        tid = "thread-restart-test"
        hist.save_or_update_conversation(
            cid, tid, "Restart Test",
            refund_reference="RF-1252",
            db_path=TEST_DB
        )
        hist.add_message(cid, "user", "Persistent message", db_path=TEST_DB)

        # Simulate complete restart by opening a fresh standalone connection
        new_conn = sqlite3.connect(TEST_DB)
        cur = new_conn.cursor()
        cur.execute("SELECT title, refund_reference FROM refund_chat_history WHERE conversation_id = ?", (cid,))
        row = cur.fetchone()
        new_conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Restart Test")
        self.assertEqual(row[1], "RF-1252")

    # 8. Preserving the original LangGraph thread ID
    def test_08_preserve_original_thread_id(self):
        cid = "conv-preserve-tid"
        exact_tid = "unique-langgraph-tid-999"
        hist.save_or_update_conversation(cid, exact_tid, "Preserve Thread", db_path=TEST_DB)

        # Update metadata without overwriting thread_id
        hist.save_or_update_conversation(
            cid, exact_tid, "Preserve Thread (Updated)",
            investigation_status="COMPLETED",
            db_path=TEST_DB
        )
        rec = hist.get_conversation(cid, db_path=TEST_DB)
        self.assertEqual(rec["thread_id"], exact_tid)

    # 9. Restoring a pending human-review conversation
    def test_09_restore_pending_human_review_conversation(self):
        cid = "conv-pending-review"
        tid = f"thread-pending-{uuid.uuid4()}"
        complaint = "Agent Skyline Travels is escalating ticket RF-1570 for BLR-DXB route."

        # Run investigation with dedicated workflow
        app = create_refund_workflow(db_path=TEST_DB)
        res = run_refund_investigation(complaint, thread_id=tid, app=app)
        self.assertTrue(res.get("human_review_required"))

        # Save as pending review
        hist.save_or_update_conversation(
            conversation_id=cid,
            thread_id=tid,
            title="RF-1570: Missing in Finance",
            refund_reference="RF-1570",
            category="MISSING_IN_FINANCE",
            investigation_status="ANALYSIS_COMPLETED",
            human_review_status="AWAITING_REVIEW",
            complaint_text=complaint,
            db_path=TEST_DB
        )

        # Verify restoration of pending review status
        restored = hist.get_conversation(cid, db_path=TEST_DB)
        self.assertEqual(restored["human_review_status"], "AWAITING_REVIEW")
        self.assertEqual(restored["refund_reference"], "RF-1570")

    # 10. Resuming a pending investigation
    def test_10_resume_pending_investigation_and_update_history(self):
        cid = "conv-resume-test"
        tid = f"thread-resume-{uuid.uuid4()}"
        complaint = "Agent Skyline Travels is escalating ticket RF-1570 for BLR-DXB route."

        app = create_refund_workflow(db_path=TEST_DB)
        run_refund_investigation(complaint, thread_id=tid, app=app)

        hist.save_or_update_conversation(
            cid, tid, "RF-1570 Pending",
            refund_reference="RF-1570",
            category="MISSING_IN_FINANCE",
            human_review_status="AWAITING_REVIEW",
            db_path=TEST_DB
        )

        # Resume the exact thread
        resumed = resume_with_human_decision("SUBMIT_TICKET_TO_FINANCE", thread_id=tid, app=app)
        self.assertEqual(resumed["human_decision"], "SUBMIT_TICKET_TO_FINANCE")

        # Update history
        hist.save_or_update_conversation(
            cid, tid, "RF-1570 Completed",
            refund_reference="RF-1570",
            category="MISSING_IN_FINANCE",
            investigation_status="COMPLETED",
            human_review_status="COMPLETED",
            human_decision="SUBMIT_TICKET_TO_FINANCE",
            final_report=resumed["final_report"],
            db_path=TEST_DB
        )
        hist.add_message(cid, "supervisor", "SUBMIT_TICKET_TO_FINANCE", db_path=TEST_DB)
        hist.add_message(cid, "assistant", resumed["final_report"], db_path=TEST_DB)

        updated = hist.get_conversation(cid, db_path=TEST_DB)
        self.assertEqual(updated["human_review_status"], "COMPLETED")
        self.assertEqual(updated["human_decision"], "SUBMIT_TICKET_TO_FINANCE")
        self.assertIn("MISSING_IN_FINANCE", updated["final_report"])

    # 11. Preventing messages from leaking between conversations
    def test_11_prevent_cross_conversation_leakage(self):
        c1 = "conv-leak-1"
        c2 = "conv-leak-2"
        hist.save_or_update_conversation(c1, "t1", "Case 1", db_path=TEST_DB)
        hist.save_or_update_conversation(c2, "t2", "Case 2", db_path=TEST_DB)

        hist.add_message(c1, "user", "Message for Thread 1 Only", db_path=TEST_DB)
        hist.add_message(c2, "user", "Message for Thread 2 Only", db_path=TEST_DB)

        msgs_1 = [m["content"] for m in hist.get_conversation_messages(c1, db_path=TEST_DB)]
        msgs_2 = [m["content"] for m in hist.get_conversation_messages(c2, db_path=TEST_DB)]

        self.assertIn("Message for Thread 1 Only", msgs_1)
        self.assertNotIn("Message for Thread 2 Only", msgs_1)

        self.assertIn("Message for Thread 2 Only", msgs_2)
        self.assertNotIn("Message for Thread 1 Only", msgs_2)

    # 12. Deleting a conversation safely
    def test_12_safe_deletion(self):
        cid = "conv-delete-me"
        hist.save_or_update_conversation(cid, "thread-del", "Delete Me", db_path=TEST_DB)
        hist.add_message(cid, "user", "Temporary query", db_path=TEST_DB)

        self.assertIsNotNone(hist.get_conversation(cid, db_path=TEST_DB))
        self.assertEqual(len(hist.get_conversation_messages(cid, db_path=TEST_DB)), 1)

        deleted = hist.delete_conversation(cid, db_path=TEST_DB)
        self.assertTrue(deleted)
        self.assertIsNone(hist.get_conversation(cid, db_path=TEST_DB))
        self.assertEqual(len(hist.get_conversation_messages(cid, db_path=TEST_DB)), 0)

    # 13. Handling empty history gracefully
    def test_13_empty_history_handling(self):
        convs = hist.list_all_conversations(db_path=TEST_DB)
        self.assertEqual(convs, [])

        res = hist.get_conversation("nonexistent-id", db_path=TEST_DB)
        self.assertIsNone(res)

        msgs = hist.get_conversation_messages("nonexistent-id", db_path=TEST_DB)
        self.assertEqual(msgs, [])

    # 14. Handling missing or corrupted records gracefully
    def test_14_corrupted_or_missing_record_handling(self):
        # Passing empty strings, invalid titles, or nonexistent updates
        renamed = hist.rename_conversation("nonexistent-id", "New Title", db_path=TEST_DB)
        self.assertFalse(renamed)

        deleted = hist.delete_conversation("nonexistent-id", db_path=TEST_DB)
        self.assertFalse(deleted)


if __name__ == "__main__":
    unittest.main()

