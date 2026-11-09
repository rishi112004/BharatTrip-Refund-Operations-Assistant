"""
test_live_groq_connectivity.py
------------------------------
Real live API connectivity test against Groq.
Distinguished from offline mocked unit tests.
Invokes ChatGroq with configured model (openai/gpt-oss-120b) to verify real network & account availability.
"""

from __future__ import annotations

import os
import sys
import unittest

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import bharattrip_groq_config as groq_cfg


class TestLiveGroqConnectivity(unittest.TestCase):
    """Verifies actual live Groq API connectivity when GROQ_API_KEY is present."""

    def test_live_groq_api_call(self):
        key = groq_cfg.get_groq_api_key()
        if not key:
            self.skipTest("GROQ_API_KEY not set in environment or .env; skipping live network test.")

        result = groq_cfg.verify_groq_connectivity(test_prompt="Reply with 'GROQ_ONLINE'")
        self.assertTrue(result["connected"], f"Live API connection failed: {result.get('message')}")
        self.assertEqual(result["status"], "OK")
        self.assertIn("openai/gpt-oss-120b", result["model"])
        self.assertIsNotNone(result["sample_response"])
        print(f"\n[LIVE API VERIFIED] Model: {result['model']} | Response: {result['sample_response']} | Key: {result['masked_key']}")


if __name__ == "__main__":
    unittest.main()

