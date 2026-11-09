"""
test_bharattrip_chatbot.py
--------------------------
Unit and integration tests for BharatTrip AI Refund Chatbot and Groq configuration.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import bharattrip_groq_config as groq_cfg
import bharattrip_history as hist
from bharattrip_chatbot import BharatTripChatbotService, BLOCKED_EXECUTION_NOTICE
from bharattrip_reconciliation import BharatTripReconciliationEngine

TEST_BOT_DB = "test_bot_temp.db"


class MockChatGroq:
    """Deterministic Mock LLM simulating Groq responses."""
    def __init__(self, response_text: str = "Mocked Groq response"):
        self.response_text = response_text
        self.invoked = False
        self.last_messages = []

    def invoke(self, messages):
        self.invoked = True
        self.last_messages = messages
        class MockResp:
            content = self.response_text
        return MockResp()


class TestBharatTripChatbot(unittest.TestCase):
    """Test suite for Groq configuration, prompt grounding, and guardrails."""

    @classmethod
    def setUpClass(cls):
        if os.path.exists(TEST_BOT_DB):
            try:
                os.remove(TEST_BOT_DB)
            except Exception:
                pass
        hist.init_history_db(TEST_BOT_DB)
        cls.engine = BharatTripReconciliationEngine()

    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        if os.path.exists(TEST_BOT_DB):
            try:
                os.remove(TEST_BOT_DB)
            except Exception:
                pass

    # 1. Groq Configuration & Model Resolution
    def test_groq_configuration_resolution(self):
        model = groq_cfg.get_configured_model()
        self.assertIn("gpt-oss-120b", model.lower())

    # 2. Masked API Key Protection
    def test_masked_api_key_protection(self):
        masked = groq_cfg.get_masked_api_key()
        if groq_cfg.get_groq_api_key():
            self.assertIn("...", masked)
            # Must not expose the full secret key
            self.assertNotEqual(masked, groq_cfg.get_groq_api_key())

    # 3. Missing API Key Friendly Handling
    def test_missing_api_key_handling(self):
        old_key = os.environ.get("GROQ_API_KEY")
        try:
            os.environ.pop("GROQ_API_KEY", None)
            res = groq_cfg.verify_groq_connectivity()
            self.assertFalse(res["connected"])
            self.assertEqual(res["status"], "MISSING_KEY")
            self.assertIn("missing", res["message"].lower())
        finally:
            if old_key:
                os.environ["GROQ_API_KEY"] = old_key

    # 4. Refusal of Financial Action Requests
    def test_refusal_of_financial_execution_requests(self):
        bot = BharatTripChatbotService(reconciliation_engine=self.engine, db_path=TEST_BOT_DB)
        prohibited_queries = [
            "Please approve the refund for RF-1286 immediately.",
            "Execute payment payout now for ticket RF-1570",
            "Disburse funds for travel agent Wander Agency",
            "Force refund transfer of INR 10000"
        ]
        for q in prohibited_queries:
            res = bot.answer_query(q)
            self.assertTrue(res["is_blocked"], f"Expected blocked response for: {q}")
            self.assertIn("REQUEST BLOCKED", res["answer"])
            self.assertIn("advisory reconciliation tool", res["answer"])

    # 5. Grounded Ticket Inquiry Answering (RF-1570)
    def test_grounded_ticket_inquiry_rf_1570(self):
        mock_llm = MockChatGroq("RF-1570 is missing in Finance records.")
        bot = BharatTripChatbotService(reconciliation_engine=self.engine, db_path=TEST_BOT_DB, llm=mock_llm)

        res = bot.answer_query("Why is RF-1570 awaiting review?")
        self.assertEqual(res["referenced_tickets"], ["RF-1570"])
        self.assertTrue(mock_llm.invoked)

        # Verify that grounded context contained verified Support amount and status
        system_content = str(mock_llm.last_messages[0].content)
        self.assertIn("RF-1570", system_content)
        self.assertIn("MISSING_IN_FINANCE", system_content)
        self.assertIn("23600", system_content)

    # 6. Grounded Ticket Inquiry Answering (RF-1252)
    def test_grounded_ticket_inquiry_rf_1252(self):
        mock_llm = MockChatGroq("RF-1252 has an explained partial deduction.")
        bot = BharatTripChatbotService(reconciliation_engine=self.engine, db_path=TEST_BOT_DB, llm=mock_llm)

        res = bot.answer_query("What happened to RF-1252?")
        self.assertEqual(res["referenced_tickets"], ["RF-1252"])
        system_content = str(mock_llm.last_messages[0].content)
        self.assertIn("21100", system_content)
        self.assertIn("19400", system_content)
        self.assertIn("1700", system_content)
        self.assertIn("EXPLAINED_DEDUCTION", system_content)

    # 7. Grounded Duplicate Inquiry
    def test_grounded_duplicate_inquiry(self):
        mock_llm = MockChatGroq("Multiple duplicate Finance records exist.")
        bot = BharatTripChatbotService(reconciliation_engine=self.engine, db_path=TEST_BOT_DB, llm=mock_llm)

        res = bot.answer_query("Which cases have duplicate Finance records?")
        self.assertTrue(mock_llm.invoked)
        system_content = str(mock_llm.last_messages[0].content)
        self.assertIn("Duplicate Finance Reference IDs", system_content)
        self.assertIn("RF-1148", system_content)

    # 8. Unknown / Nonexistent Reference Handling
    def test_unknown_ticket_reference_handling(self):
        mock_llm = MockChatGroq("Ticket was not found.")
        bot = BharatTripChatbotService(reconciliation_engine=self.engine, db_path=TEST_BOT_DB, llm=mock_llm)

        res = bot.answer_query("What is the status of invalid ticket RF-999999?")
        self.assertEqual(res["referenced_tickets"], ["RF-999999"])
        system_content = str(mock_llm.last_messages[0].content)
        self.assertIn("NOT_FOUND", system_content)

    # 9. Persistent Message Storage in SQLite History
    def test_chat_message_persistence(self):
        bot = BharatTripChatbotService(reconciliation_engine=self.engine, db_path=TEST_BOT_DB)
        conv_id = f"test-conv-{uuid.uuid4()}"
        thread_id = f"test-thread-{uuid.uuid4()}"

        hist.save_or_update_conversation(
            conversation_id=conv_id,
            thread_id=thread_id,
            title="Chatbot Test Session",
            db_path=TEST_BOT_DB
        )

        bot.answer_query("Why is RF-1570 awaiting review?", conversation_id=conv_id)
        messages = hist.get_conversation_messages(conv_id, db_path=TEST_BOT_DB)
        self.assertGreaterEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
