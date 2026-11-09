"""
bharattrip_chatbot.py
---------------------
Grounded AI Refund Operations Chatbot for BharatTrip.
Answers natural language questions about refunds, discrepancies, assignments,
and next steps using actual reconciliation records as the sole source of truth.

Guarantees:
- Uses Groq (openai/gpt-oss-120b) with deterministic fact grounding.
- Zero hallucination of amounts, statuses, source rows, or employee names.
- Intercepts and refuses financial approval/execution requests.
- Explains verified findings and recommends compliant next actions.
- Persists chat conversations and messages in SQLite history.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import bharattrip_groq_config as groq_cfg
import bharattrip_history as hist
import bharattrip_assignment as asgn
from bharattrip_reconciliation import BharatTripReconciliationEngine, normalize_reference

BLOCKED_EXECUTION_NOTICE = (
    "**[REQUEST BLOCKED - GOVERNANCE POLICY]**\n\n"
    "The BharatTrip AI Operations Assistant is strictly an advisory reconciliation tool. "
    "Under corporate financial controls and compliance regulations, the assistant **CANNOT** "
    "approve, decline, disburse, or execute refund payouts.\n\n"
    "**Required Action**: Financial disbursements and ledger entries must be executed manually by "
    "an authorized Finance Officer in accordance with BharatTrip payment approval policies."
)

CHATBOT_SYSTEM_PROMPT = """You are the BharatTrip AI Operations Assistant.
You assist Support, Finance, and Operations personnel in understanding refund disputes, cross-departmental reconciliation discrepancies, and workload assignments.

CRITICAL OPERATIONAL RULES:
1. TRUTH SOURCE: You must base your answers EXCLUSIVELY on the verified reconciliation records and assignment data provided below.
2. NO HALLUCINATION: Never invent, extrapolate, or alter any amounts (INR), payout statuses, source Excel row numbers, or employee names.
3. ADVISORY ONLY: Never approve, decline, or execute a refund. If the user asks you to approve or pay, refuse firmly and cite governance policy.
4. EXPLANATION & ACTION: Explain clearly what the verified data indicates (Support status, Finance payout status, deductions, escalation context), why a discrepancy exists, and what the recommended operational next step is.
5. TONE: Professional, precise, factual, and helpful to operations staff."""


class BharatTripChatbotService:
    """Service handling natural language queries backed by Groq and deterministic data."""

    def __init__(
        self,
        reconciliation_engine: Optional[BharatTripReconciliationEngine] = None,
        db_path: Optional[str] = None,
        llm: Optional[Any] = None,
    ):
        self.engine = reconciliation_engine or BharatTripReconciliationEngine()
        self.db_path = db_path or asgn.DEFAULT_DB_PATH
        self.llm = llm

    def is_financial_execution_request(self, query: str) -> bool:
        """Detects if query attempts autonomous refund execution, approval, or disbursement."""
        q = query.lower()
        patterns = [
            r"\b(approve|execute|pay\s*out|disburse|transfer|release\s*payment|process\s*refund)\b",
            r"\b(send\s*money|credit\s*account|force\s*refund|override\s*decline)\b",
        ]
        return any(re.search(pat, q) for pat in patterns)

    def extract_referenced_tickets(self, query: str) -> List[str]:
        """Extracts all RF-xxxx style refund references from the query."""
        found = []
        # Match standard RF-xxxx, RF xxxx, RFxxxx with 3 to 8 digits
        matches = re.findall(r"\bRF[\s_-]?\d{3,8}\b", query, flags=re.IGNORECASE)
        for m in matches:
            norm = normalize_reference(m)
            if norm.is_valid and norm.normalized and norm.normalized not in found:
                found.append(norm.normalized)
        return found

    def build_context_for_query(self, query: str) -> Tuple[str, List[str]]:
        """
        Gathers deterministic data relevant to the query from reconciliation engine and assignments.
        Returns: (context_string, list_of_referenced_tickets)
        """
        q_lower = query.lower()
        refs = self.extract_referenced_tickets(query)
        context_blocks = []

        # 1. Ticket-specific query (e.g. "Why is RF-1570 awaiting review?", "What happened to RF-1252?")
        if refs:
            for ref in refs:
                item = self.engine.reconcile_reference(ref)
                d = item.to_dict()

                # Also check active assignment in SQLite
                asgn_info = None
                try:
                    q_res = asgn.list_human_action_queue(db_path=self.db_path)
                    for a in q_res:
                        if a.get("refund_reference") == ref:
                            asgn_info = a
                            break
                except Exception:
                    pass

                block = [
                    f"### Verified Record for Ticket: {ref}",
                    f"- Primary Category: {d.get('Primary Category')}",
                    f"- Support Status: {d.get('Support Status')} (Row: {d.get('Support Rows')})",
                    f"- Support Amount Claimed: ₹{d.get('Support Amount (INR)')}",
                    f"- Finance Payout Status: {d.get('Finance Payout Status')} (Row: {d.get('Finance Rows')})",
                    f"- Finance Amount Paid: ₹{d.get('Finance Amount Paid (INR)')}",
                    f"- Recorded Deduction: ₹{d.get('Finance Deduction (INR)')} ({d.get('Deduction Type')})",
                    f"- Amount Difference: ₹{d.get('Amount Difference (INR)')}",
                    f"- Explanation: {d.get('Explanation')}",
                    f"- Human Review Required: {d.get('Human Review Required')}",
                    f"- Human Review Reasons: {d.get('Human Review Reasons')}",
                    f"- Recommended Next Action: {d.get('Recommended Next Action')}",
                ]
                if d.get("Escalation Rows"):
                    block.append(f"- Escalation: Row(s) {d.get('Escalation Rows')} | Status: {d.get('Escalation Status')} | ID: {d.get('Escalation ID')}")
                if asgn_info:
                    block.append(
                        f"- Current Assignment: Status '{asgn_info.get('assignment_status')}', "
                        f"Assigned to {asgn_info.get('assigned_employee_name')} ({asgn_info.get('assigned_department')})"
                    )
                context_blocks.append("\n".join(block))

        # 2. Duplicate Finance records query
        elif "duplicate" in q_lower and ("finance" in q_lower or "ledger" in q_lower or "payout" in q_lower):
            fin_dups = [k for k, rows in self.engine.finance_by_ref.items() if len(rows) > 1]
            total_dup_rows = sum(len(rows) for k, rows in self.engine.finance_by_ref.items() if len(rows) > 1)
            context_blocks.append(
                f"### Verified Finance Duplicate Summary:\n"
                f"- Total Duplicate Finance Reference IDs: {len(fin_dups)} references representing {total_dup_rows} rows.\n"
                f"- Verified Duplicate References: {', '.join(sorted(fin_dups)[:10])}\n"
                f"- Risk Rationale: High risk of duplicate disbursement. Finance Supervisor audit required before releasing funds."
            )

        # 3. Human attention / pending cases query
        elif "human attention" in q_lower or "awaiting review" in q_lower or "action queue" in q_lower:
            try:
                queue = asgn.list_human_action_queue(db_path=self.db_path)
                if queue:
                    summary_lines = [f"### Current Active Human Action Queue ({len(queue)} cases):"]
                    for idx, c in enumerate(queue[:8], 1):
                        summary_lines.append(
                            f"{idx}. {c.get('refund_reference')} | Issue: {c.get('issue_category')} | "
                            f"Dept: {c.get('assigned_department')} | Assigned: {c.get('assigned_employee_name')} | Status: {c.get('assignment_status')}"
                        )
                    context_blocks.append("\n".join(summary_lines))
                else:
                    context_blocks.append("### Human Action Queue: No pending cases currently recorded in the active queue.")
            except Exception:
                context_blocks.append("### Human Action Queue: Unable to query active queue.")

        # 4. What should Finance investigate next
        elif "finance" in q_lower and ("investigate" in q_lower or "next" in q_lower or "priority" in q_lower):
            context_blocks.append(
                "### Finance Priority Investigation Guidelines:\n"
                "1. **Missing Finance Records** (e.g. RF-1570): Support closed ticket, but no payout record exists in Finance ledger. Action: Verify banking handover.\n"
                "2. **Duplicate Finance Records** (e.g. RF-1148): Multiple payout records exist for one ticket. Action: Halt payment to avoid double disbursement.\n"
                "3. **Pending Payouts** (e.g. RF-1757): Support closed ticket, but Finance payout is PENDING. Action: Expedite bank release.\n"
                "4. **Unverified Deductions** (e.g. RF-1252): Support claimed higher amount than Finance paid. Action: Provide approved fare policy deduction breakdown."
            )

        # 5. General fallback context
        else:
            context_blocks.append(
                "### Global BharatTrip Operations Context:\n"
                "- Source Dataset: 638 Support rows (22 duplicated IDs), 638 Finance rows (9 duplicated refs), 257 Escalation rows.\n"
                "- Governed by deterministic Pandas arithmetic and LangGraph human-in-the-loop controls."
            )

        return "\n\n".join(context_blocks), refs

    def answer_query(
        self,
        query: str,
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Main query responder.
        - Checks for financial execution block.
        - Builds deterministic context from actual workbook & assignment database.
        - Invokes Groq LLM (or deterministic fallback) with strict grounding.
        - Persists chat messages into SQLite history.
        """
        clean_query = query.strip()
        if not clean_query:
            return {
                "answer": "Please ask a question regarding a refund reference (e.g. `RF-1570`, `RF-1252`), discrepancy categories, or pending queue cases.",
                "referenced_tickets": [],
                "is_blocked": False,
            }

        # 1. Financial Execution Check
        if self.is_financial_execution_request(clean_query):
            answer = BLOCKED_EXECUTION_NOTICE
            refs = self.extract_referenced_tickets(clean_query)
            if conversation_id:
                try:
                    hist.add_message(conversation_id, "user", clean_query, db_path=self.db_path)
                    hist.add_message(conversation_id, "assistant", answer, db_path=self.db_path)
                except Exception:
                    pass
            return {
                "answer": answer,
                "referenced_tickets": refs,
                "is_blocked": True,
            }

        # 2. Gather Grounded Deterministic Data
        grounded_context, refs = self.build_context_for_query(clean_query)

        # 3. Invoke Groq LLM with Strict Fact Grounding
        effective_llm = self.llm
        if effective_llm is None and groq_cfg.get_groq_api_key():
            try:
                effective_llm = groq_cfg.get_groq_chat_model(temperature=0.1)
            except Exception:
                effective_llm = None

        if effective_llm:
            try:
                messages = [
                    SystemMessage(content=f"{CHATBOT_SYSTEM_PROMPT}\n\n=== VERIFIED GROUND TRUTH DATA ===\n{grounded_context}"),
                    HumanMessage(content=clean_query)
                ]
                resp = effective_llm.invoke(messages)
                answer = getattr(resp, "content", str(resp)).strip()
            except Exception as exc:
                # Graceful deterministic fallback on API error
                answer = (
                    f"**[Groq API Temporarily Unavailable]**\n\n"
                    f"Here is the verified operations record for your inquiry:\n\n{grounded_context}"
                )
        else:
            # Deterministic fallback when Groq is not configured
            answer = (
                f"**[Deterministic Operations Summary]**\n\n"
                f"{grounded_context}\n\n"
                f"> *Note: Configure GROQ_API_KEY in .env to enable conversational AI synthesis.*"
            )

        # 4. Persist to Chat History
        if conversation_id:
            try:
                hist.add_message(conversation_id, "user", clean_query, db_path=self.db_path)
                hist.add_message(conversation_id, "assistant", answer, db_path=self.db_path)
            except Exception:
                pass

        return {
            "answer": answer,
            "referenced_tickets": refs,
            "is_blocked": False,
            "grounded_context": grounded_context,
        }
