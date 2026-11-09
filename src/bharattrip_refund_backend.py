"""
bharattrip_refund_backend.py
----------------------------
Dedicated LangGraph workflow for BharatTrip AI-powered refund operations.
Integrates the deterministic reconciliation engine (bharattrip_reconciliation.py)
with ChatGroq for complaint understanding, structured extraction, deterministic
financial reconciliation, human-in-the-loop interruption, and advisory reporting.
"""

from __future__ import annotations

import os
import re
import sys
import sqlite3
from typing import Annotated, Any, Dict, List, Optional, TypedDict

# Ensure current src directory is in sys.path for robust standalone and module imports
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

import bharattrip_groq_config as groq_cfg
import bharattrip_assignment as asgn
from bharattrip_reconciliation import (
    BharatTripReconciliationEngine,
    ReconciliationItem,
    normalize_reference,
)

load_dotenv()


def get_default_db_path() -> str:
    """Resolves operations SQLite database in package data/ directory or CWD."""
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_data = os.path.join(pkg_root, "data")
    if os.path.exists(target_data):
        return os.path.join(target_data, "bharattrip_operations.db")
    return "bharattrip_operations.db"

DEFAULT_DB_PATH = get_default_db_path()


# ==============================================================================
# 1. STATE DEFINITION
# ==============================================================================

class RefundState(TypedDict):
    """Rich typed state for the BharatTrip refund operations workflow."""
    messages: Annotated[List[BaseMessage], add_messages]
    complaint_text: str
    investigation_thread_id: Optional[str]
    extracted_information: Dict[str, Any]
    refund_reference: Optional[str]
    classification: Dict[str, Any]
    missing_fields: List[str]
    reconciliation_result: Optional[Dict[str, Any]]
    issue_flags: List[str]
    human_review_required: bool
    investigation_status: str
    final_report: Optional[str]
    human_decision: Optional[str]
    assigned_department: Optional[str]
    assigned_employee_id: Optional[str]
    assigned_employee_name: Optional[str]
    assignment_id: Optional[str]
    assignment_status: Optional[str]
    assignment_method: Optional[str]


# ==============================================================================
# 2. PROMPT & EXTRACTION HELPERS
# ==============================================================================

EXTRACTION_SYSTEM_PROMPT = """You are an AI assistant specialized in analyzing B2B travel refund complaints for BharatTrip.
Analyze the provided complaint or inquiry and extract the following details accurately:
- refund_reference: Look for ticket codes like RF-1234, RF 1234, RF1234. If multiple different refund references appear, return "AMBIGUOUS". If none mentioned, return null.
- agent_name: Name of travel agency or agent (e.g. Wander Agency, TripHub, GoFly Holidays).
- route: Airport or city route (e.g. DEL-KTM, COK-DXB, BLR-MAA).
- amount: Stated amount in INR if mentioned (numeric only, e.g. 15000), else null.
- complaint_details: Summary of what the complaint or question is about.
- category: One of [STATUS_CHASE, DEDUCTION_DISPUTE, MISSING_PAYOUT, REJECTED_DISPUTE, GENERAL_INQUIRY, EXECUTION_REQUEST].
- urgency: One of [HIGH, MEDIUM, LOW]. If words like 'urgent', 'weeks', 'ignored', 'dispute' appear, set HIGH.

Respond strictly in valid JSON format with these exact keys:
{
  "refund_reference": "...",
  "agent_name": "...",
  "route": "...",
  "amount": null,
  "complaint_details": "...",
  "category": "...",
  "urgency": "..."
}"""


def extract_info_with_fallback(text: str, llm: Optional[Any] = None) -> Dict[str, Any]:
    """
    Extracts complaint entities using ChatGroq if available,
    with a deterministic regex fallback for offline/test reliability.
    """
    extracted: Dict[str, Any] = {
        "refund_reference": None,
        "agent_name": None,
        "route": None,
        "amount": None,
        "complaint_details": text,
        "category": "GENERAL_INQUIRY",
        "urgency": "MEDIUM",
    }

    # Deterministic regex search for reference
    norm_res = normalize_reference(text)
    if norm_res.is_valid and norm_res.normalized:
        extracted["refund_reference"] = norm_res.normalized
    elif norm_res.is_ambiguous:
        extracted["refund_reference"] = "AMBIGUOUS"
        extracted["candidate_refs"] = norm_res.candidate_refs

    # Deterministic regex for route (e.g. DEL-BOM, BOM-DXB)
    route_match = re.search(r"\b([A-Z]{3}\s*[-/]\s*[A-Z]{3})\b", text)
    if route_match:
        extracted["route"] = re.sub(r"\s+", "", route_match.group(1)).replace("/", "-")

    # Deterministic regex for amount (e.g. Rs 15000, ₹15,000, 15000 INR)
    amt_match = re.search(r"(?:Rs\.?|INR|₹)?\s*(\d{1,3}(?:,\d{3})+|\d{3,6})\b", text, flags=re.IGNORECASE)
    if amt_match:
        try:
            extracted["amount"] = float(amt_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # If LLM is provided and api key exists, enrich extraction
    if llm and os.getenv("GROQ_API_KEY"):
        try:
            prompt = [
                SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=f"Analyze this complaint:\n{text}")
            ]
            response = llm.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)

            # Extract json block
            json_match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if json_match:
                import json
                parsed = json.loads(json_match.group(0))
                # Validate and merge LLM outputs (never overwrite deterministic ambiguous flag)
                llm_ref = parsed.get("refund_reference")
                if norm_res.is_ambiguous:
                    extracted["refund_reference"] = "AMBIGUOUS"
                    extracted["candidate_refs"] = norm_res.candidate_refs
                elif llm_ref and str(llm_ref).strip().upper() == "AMBIGUOUS":
                    extracted["refund_reference"] = "AMBIGUOUS"
                elif llm_ref and str(llm_ref).lower() != "null":
                    llm_norm = normalize_reference(llm_ref)
                    if llm_norm.is_valid and llm_norm.normalized:
                        extracted["refund_reference"] = llm_norm.normalized
                    elif llm_norm.is_ambiguous:
                        extracted["refund_reference"] = "AMBIGUOUS"
                        extracted["candidate_refs"] = llm_norm.candidate_refs
                if parsed.get("agent_name"):
                    extracted["agent_name"] = parsed["agent_name"]
                if parsed.get("route"):
                    extracted["route"] = parsed["route"]
                if parsed.get("amount") is not None:
                    try:
                        extracted["amount"] = float(parsed["amount"])
                    except (ValueError, TypeError):
                        pass
                if parsed.get("category"):
                    extracted["category"] = parsed["category"]
                if parsed.get("urgency"):
                    extracted["urgency"] = parsed["urgency"]
                if parsed.get("complaint_details"):
                    extracted["complaint_details"] = parsed["complaint_details"]
        except Exception:
            # Fall back to regex-derived extraction gracefully
            pass

    # Detect execution/payout requests
    if re.search(r"\b(approve|execute|pay out|disburse|transfer money|refund now)\b", text, flags=re.IGNORECASE):
        extracted["category"] = "EXECUTION_REQUEST"
        extracted["urgency"] = "HIGH"

    return extracted


# ==============================================================================
# 3. WORKFLOW NODE IMPLEMENTATIONS
# ==============================================================================

def create_refund_workflow(
    reconciliation_engine: Optional[BharatTripReconciliationEngine] = None,
    llm: Optional[Any] = None,
    db_path: Optional[str] = None
) -> Any:
    """
    Constructs and compiles the BharatTrip Refund Operations LangGraph workflow.
    """
    active_db = db_path or DEFAULT_DB_PATH
    engine = reconciliation_engine or BharatTripReconciliationEngine()

    if llm is None and groq_cfg.get_groq_api_key():
        try:
            llm = groq_cfg.get_groq_chat_model(temperature=0)
        except Exception:
            llm = None

    # --------------------------------------------------------------------------
    # Node 1: Complaint Intake Node
    # --------------------------------------------------------------------------
    def complaint_intake_node(state: RefundState) -> Dict[str, Any]:
        """Extracts text from latest user message and initializes state."""
        messages = state.get("messages", [])
        complaint_text = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
                complaint_text = str(msg.content)
                break
            elif isinstance(msg, dict) and msg.get("role") in {"user", "human"}:
                complaint_text = str(msg.get("content", ""))
                break

        if not complaint_text and state.get("complaint_text"):
            complaint_text = state["complaint_text"]

        return {
            "complaint_text": complaint_text,
            "investigation_thread_id": state.get("investigation_thread_id", ""),
            "investigation_status": "INTAKE_COMPLETED",
            "issue_flags": list(state.get("issue_flags") or []),
            "missing_fields": list(state.get("missing_fields") or []),
            "human_review_required": False,
        }

    # --------------------------------------------------------------------------
    # Node 2: Information Extraction Node
    # --------------------------------------------------------------------------
    def information_extraction_node(state: RefundState) -> Dict[str, Any]:
        """Uses Groq + regex fallback to extract reference, amounts, agent, route."""
        text = state.get("complaint_text", "")
        extracted = extract_info_with_fallback(text, llm=llm)

        raw_ref = extracted.get("refund_reference")
        norm_ref = None

        if raw_ref == "AMBIGUOUS":
            norm_ref = "AMBIGUOUS"
        elif raw_ref:
            norm_res = normalize_reference(raw_ref)
            if norm_res.is_valid:
                norm_ref = norm_res.normalized

        classification = {
            "category": extracted.get("category", "GENERAL_INQUIRY"),
            "urgency": extracted.get("urgency", "MEDIUM"),
        }

        return {
            "extracted_information": extracted,
            "refund_reference": norm_ref,
            "classification": classification,
            "investigation_status": "EXTRACTION_COMPLETED",
        }

    # --------------------------------------------------------------------------
    # Node 3: Missing Information Check Node
    # --------------------------------------------------------------------------
    def missing_information_check_node(state: RefundState) -> Dict[str, Any]:
        """Checks if essential refund reference is available and unambiguous."""
        missing = []
        ref = state.get("refund_reference")
        issue_flags = list(state.get("issue_flags") or [])

        if not ref or ref in {"MISSING", "None", ""}:
            missing.append("refund_reference")
            issue_flags.append("MISSING_REFUND_REFERENCE")
        elif ref == "AMBIGUOUS":
            missing.append("ambiguous_reference")
            issue_flags.append("AMBIGUOUS_REFERENCE")

        return {
            "missing_fields": missing,
            "issue_flags": list(set(issue_flags)),
            "investigation_status": "VALIDATION_CHECKED",
        }

    # --------------------------------------------------------------------------
    # Node 4: Reconciliation Node
    # --------------------------------------------------------------------------
    def reconciliation_node(state: RefundState) -> Dict[str, Any]:
        """Invokes the deterministic reconciliation engine against the normalized reference."""
        ref = state.get("refund_reference", "")
        recon_item = engine.reconcile_reference(ref)
        recon_dict = recon_item.to_dict()

        # Collect issue flags
        combined_flags = list(set((state.get("issue_flags") or []) + recon_item.issue_flags))

        return {
            "reconciliation_result": recon_dict,
            "issue_flags": combined_flags,
            "investigation_status": "RECONCILIATION_COMPLETED",
        }

    # --------------------------------------------------------------------------
    # Node 5: Discrepancy Analysis Node
    # --------------------------------------------------------------------------
    def discrepancy_analysis_node(state: RefundState) -> Dict[str, Any]:
        """Evaluates whether human supervisor review or escalations are required."""
        recon = state.get("reconciliation_result") or {}
        issue_flags = list(state.get("issue_flags") or [])
        human_review = bool(recon.get("Needs Human Review", False))

        classification = dict(state.get("classification") or {})
        if classification.get("category") == "EXECUTION_REQUEST":
            human_review = True
            issue_flags.append("EXECUTION_REQUEST_BLOCKED")

        return {
            "human_review_required": human_review,
            "issue_flags": list(set(issue_flags)),
            "investigation_status": "ANALYSIS_COMPLETED",
        }

    # --------------------------------------------------------------------------
    # Node 6: Human Review Node (Human-in-the-Loop Interrupt)
    # --------------------------------------------------------------------------
    def human_review_node(state: RefundState) -> Dict[str, Any]:
        """
        Pauses graph execution using LangGraph's interrupt API.
        Surfaces structured findings to human supervisor and awaits decision.
        """
        recon = state.get("reconciliation_result") or {}
        ref = state.get("refund_reference", "UNKNOWN")
        flags = state.get("issue_flags", [])

        # Trigger automatic workload-based assignment (idempotent for this thread)
        thread_id = state.get("investigation_thread_id") or f"thread-{ref}"
        try:
            asgn_record = asgn.assign_investigation_case(
                refund_reference=ref,
                investigation_thread_id=thread_id,
                issue_category=recon.get("Primary Category", "UNKNOWN"),
                issue_flags=flags,
                db_path=active_db
            )
        except Exception:
            asgn_record = {
                "assignment_id": f"ASGN-{ref}",
                "assigned_department": "Finance",
                "assigned_employee_id": None,
                "assigned_employee_name": "Unassigned",
                "assignment_method": "FALLBACK",
                "assignment_status": "AWAITING_REVIEW",
            }

        # Formulate human review payload
        interrupt_payload = {
            "reference": ref,
            "primary_category": recon.get("Primary Category"),
            "issue_flags": flags,
            "human_review_reasons": recon.get("Human Review Reasons"),
            "support_amount": recon.get("Support Amount (INR)"),
            "finance_paid": recon.get("Finance Amount Paid (INR)"),
            "support_rows": recon.get("Support Rows"),
            "finance_rows": recon.get("Finance Rows"),
            "assigned_department": asgn_record.get("assigned_department"),
            "assigned_employee_id": asgn_record.get("assigned_employee_id"),
            "assigned_employee_name": asgn_record.get("assigned_employee_name"),
            "assignment_id": asgn_record.get("assignment_id"),
            "assignment_status": asgn_record.get("assignment_status"),
            "assignment_method": asgn_record.get("assignment_method"),
            "standard_action": "SUBMIT_TICKET_TO_FINANCE",
            "prompt": (
                f"Human review required for refund reference {ref}. "
                f"Discrepancy detected: {recon.get('Explanation')}. "
                f"Assigned to: {asgn_record.get('assigned_employee_name')} ({asgn_record.get('assigned_department')}). "
                "Please provide supervisor sign-off or action note (Standard: SUBMIT_TICKET_TO_FINANCE)."
            )
        }

        # Interrupt and pause graph
        human_decision = interrupt(interrupt_payload)

        # Upon resumption, record the decision and update assignment status to REVIEWED
        asgn_id = asgn_record.get("assignment_id")
        if asgn_id:
            try:
                asgn.update_assignment_status(
                    assignment_id=asgn_id,
                    new_status="REVIEWED",
                    changed_by="SUPERVISOR",
                    review_notes=str(human_decision),
                    change_reason="Supervisor decision submitted via LangGraph workflow resume.",
                    db_path=active_db
                )
            except Exception:
                pass

        return {
            "human_decision": str(human_decision),
            "investigation_status": "HUMAN_REVIEW_COMPLETED",
            "assigned_department": asgn_record.get("assigned_department"),
            "assigned_employee_id": asgn_record.get("assigned_employee_id"),
            "assigned_employee_name": asgn_record.get("assigned_employee_name"),
            "assignment_id": asgn_id,
            "assignment_status": "REVIEWED",
            "assignment_method": asgn_record.get("assignment_method"),
        }

    # --------------------------------------------------------------------------
    # Node 7: Final Response Node
    # --------------------------------------------------------------------------
    def final_response_node(state: RefundState) -> Dict[str, Any]:
        """Compiles the structured investigation report and appends to messages."""
        missing = state.get("missing_fields") or []
        ref = state.get("refund_reference")
        extracted = state.get("extracted_information") or {}
        classification = state.get("classification") or {}
        recon = state.get("reconciliation_result") or {}
        decision = state.get("human_decision")
        human_req = state.get("human_review_required", False)
        flags = state.get("issue_flags") or []

        # Handle missing reference
        if "refund_reference" in missing:
            msg = (
                "**BharatTrip Refund Operations Advisory**\n\n"
                "**Status**: Insufficient Information\n"
                "**Finding**: No refund reference ID was identified in your request.\n\n"
                "**Required Action**:\n"
                "Please provide a valid BharatTrip Ticket ID (e.g. `RF-1286`, `RF-1757`). "
                "Include the travel agent name and route if available to cross-verify."
            )
            return {
                "final_report": msg,
                "messages": [AIMessage(content=msg)],
                "investigation_status": "COMPLETED",
            }

        # Handle ambiguous reference
        if "ambiguous_reference" in missing:
            candidates = extracted.get("candidate_refs") or []
            msg = (
                "**BharatTrip Refund Operations Advisory**\n\n"
                "**Status**: Ambiguous Reference\n"
                f"**Finding**: Multiple potential refund reference codes were identified: `{candidates}`.\n\n"
                "**Required Action**:\n"
                "Please specify exactly one Ticket ID to perform deterministic reconciliation."
            )
            return {
                "final_report": msg,
                "messages": [AIMessage(content=msg)],
                "investigation_status": "COMPLETED",
            }

        # Construct comprehensive advisory report
        category = recon.get("Primary Category", "UNKNOWN")
        explanation = recon.get("Explanation", "")
        support_amt = recon.get("Support Amount (INR)")
        fin_paid = recon.get("Finance Amount Paid (INR)")
        deduction = recon.get("Finance Deduction (INR)")
        diff = recon.get("Amount Difference (INR)")
        support_rows = recon.get("Support Rows")
        finance_rows = recon.get("Finance Rows")
        escalation_rows = recon.get("Escalation Rows")
        agent = recon.get("Agent Name") or extracted.get("agent_name") or "N/A"
        route = recon.get("Route") or extracted.get("route") or "N/A"

        # Determine source row labels deterministically
        support_row_str = f"`{support_rows}`" if (support_rows and str(support_rows).strip()) else "None (Missing in Support)"
        finance_row_str = f"`{finance_rows}`" if (finance_rows and str(finance_rows).strip()) else "None (Missing in Finance)"

        # Escalation line deterministically extracted
        esc_rows = recon.get("Escalation Rows")
        esc_status = recon.get("Escalation Status")
        esc_id = recon.get("Escalation ID")
        esc_complaint = recon.get("Escalation Complaint")
        if esc_rows and str(esc_rows).strip():
            esc_details = f"Row(s) `{esc_rows}`"
            if esc_id:
                esc_details += f" | ID: `{esc_id}`"
            if esc_status:
                esc_details += f" | Status: `{esc_status}`"
            if esc_complaint:
                esc_details += f" | Details: {esc_complaint}"
            escalation_line = f"- **Linked Escalation Record**: {esc_details}"
        else:
            escalation_line = "- **Linked Escalation Record**: None (No linked escalation record found)"

        # Determine next action recommendation
        if category == "CLEAN_MATCH_REFUNDED":
            next_action = "No action required. Payout has already been settled in full as per Finance records."
        elif category == "EXPLAINED_DEDUCTION":
            next_action = "Communicate the specific fee deduction to the agent. Note: Supporting policy authorization remains unverified."
        elif category == "MISSING_IN_FINANCE":
            next_action = "Submit ticket immediately to Finance for intake. Inform agent that payout was delayed in Support-to-Finance handover."
        elif category == "CLOSED_BUT_PENDING":
            next_action = "Expedite payment release with Finance team. Do not re-credit or close ticket again."
        elif category == "CLOSED_BUT_DECLINED":
            next_action = "Escalate to Operations Manager to verify whether decline reason applies, and update agent honestly regarding decline."
        elif category == "DUPLICATE_IN_FINANCE":
            next_action = "Stop all processing immediately. Finance supervisor must audit both Finance rows to eliminate double-disbursement."
        elif category == "PAID_BUT_SUPPORT_WORKING":
            next_action = "Update Support ticket to 'Closed' and notify agent that money was already transferred."
        elif category == "SUPPORT_DUPLICATE_REVIEW":
            next_action = "Reconcile duplicate Support tickets to confirm correct claim amount before taking action."
        elif category == "FINANCE_ONLY_RECORDS":
            next_action = "Create back-dated Support tracking ticket for Finance transaction audit record."
        elif category == "AMOUNT_MISMATCH":
            next_action = "Review claim variance between Support and Finance with accounts team. Do not close ticket or execute payout until variance is reconciled."
        elif category == "IN_PROGRESS_OR_DECLINED_CONSISTENT":
            next_action = "No immediate payout required. Both departments reflect consistent non-refunded or in-progress status."
        else:
            next_action = "Escalate to Human Supervisor for exception review."

        review_status_str = "Completed by Human Supervisor" if decision else ("Required" if human_req else "Not Required")

        assigned_emp = state.get("assigned_employee_name")
        assigned_dept = state.get("assigned_department")
        asgn_status = state.get("assignment_status", "N/A")

        report_lines = [
            "### BharatTrip Refund Operations — Advisory Investigation Report",
            f"**Refund Reference**: `{ref}` | **Agent**: {agent} | **Route**: {route}",
            f"**Classification**: Category: `{classification.get('category')}` | Urgency: `{classification.get('urgency')}`",
            "",
            "#### 1. Audit Findings Summary",
            f"- **Primary Category**: `{category}`",
            f"- **Reconciliation Summary**: {explanation}",
            f"- **Secondary Issue Flags**: `{', '.join(flags)}`",
            "",
            "#### 2. Cross-Department Financial Details",
            f"- **Support Claim Amount**: ₹{support_amt if support_amt is not None else 'N/A'} (Excel Source Row: {support_row_str})",
            f"- **Support Recorded Status**: `{recon.get('Support Status', 'N/A')}`",
            f"- **Finance Amount Paid**: ₹{fin_paid if fin_paid is not None else 'N/A'} (Excel Source Row: {finance_row_str})",
            f"- **Finance Payout Status**: `{recon.get('Finance Payout Status', 'N/A')}`",
            f"- **Finance Deduction**: ₹{deduction if deduction is not None else 'None Recorded'} (Deduction Status: `{recon.get('Deduction Status', 'MISSING')}`)",
            f"- **Calculated Variance**: ₹{diff if diff is not None else '0.0'}",
            f"- **Deduction Arithmetic Explained**: {'Yes' if recon.get('Is Deduction Explained') else 'No'}",
            escalation_line,
            "",
            "#### 3. Operational Recommendation & Governance",
            f"- **Recommended Next Action**: {next_action}",
            f"- **Human Review Status**: `{review_status_str}`",
        ]

        if assigned_emp and assigned_emp != "None":
            report_lines.append(
                f"- **Workload Assignment**: Assigned to `{assigned_emp}` ({assigned_dept}) | Status: `{asgn_status}`"
            )

        if decision:
            report_lines.append(
                f"- **Supervisor Action Note**: `{decision}` "
                "(Recorded strictly as an internal operational audit note. "
                "This assistant does not approve, decline, or execute refunds.)"
            )

        report_lines.extend([
            "",
            "> **[GOVERNANCE POLICY & COMPLIANCE]** *This report is strictly advisory for operations auditing. "
            "This automated assistant never approves, declines, or transfers funds. "
            "Supervisor entries are recorded only as audit notes and never trigger autonomous financial execution.*"
        ])

        final_msg = "\n".join(report_lines)

        return {
            "final_report": final_msg,
            "messages": [AIMessage(content=final_msg)],
            "investigation_status": "COMPLETED",
        }

    # --------------------------------------------------------------------------
    # 4. CONDITIONAL ROUTERS
    # --------------------------------------------------------------------------
    def route_after_validation(state: RefundState) -> str:
        missing = state.get("missing_fields") or []
        if "refund_reference" in missing or "ambiguous_reference" in missing:
            return "final_response_node"
        return "reconciliation_node"

    def route_after_analysis(state: RefundState) -> str:
        human_req = state.get("human_review_required", False)
        # If human review required and not yet decided, pause at human review node
        if human_req and not state.get("human_decision"):
            return "human_review_node"
        return "final_response_node"

    # --------------------------------------------------------------------------
    # 5. GRAPH ASSEMBLY & COMPILATION
    # --------------------------------------------------------------------------
    workflow = StateGraph(RefundState)

    workflow.add_node("complaint_intake_node", complaint_intake_node)
    workflow.add_node("information_extraction_node", information_extraction_node)
    workflow.add_node("missing_information_check_node", missing_information_check_node)
    workflow.add_node("reconciliation_node", reconciliation_node)
    workflow.add_node("discrepancy_analysis_node", discrepancy_analysis_node)
    workflow.add_node("human_review_node", human_review_node)
    workflow.add_node("final_response_node", final_response_node)

    workflow.add_edge(START, "complaint_intake_node")
    workflow.add_edge("complaint_intake_node", "information_extraction_node")
    workflow.add_edge("information_extraction_node", "missing_information_check_node")

    workflow.add_conditional_edges(
        "missing_information_check_node",
        route_after_validation,
        {
            "final_response_node": "final_response_node",
            "reconciliation_node": "reconciliation_node",
        }
    )

    workflow.add_edge("reconciliation_node", "discrepancy_analysis_node")

    workflow.add_conditional_edges(
        "discrepancy_analysis_node",
        route_after_analysis,
        {
            "human_review_node": "human_review_node",
            "final_response_node": "final_response_node",
        }
    )

    workflow.add_edge("human_review_node", "final_response_node")
    workflow.add_edge("final_response_node", END)

    # Checkpointer
    conn = sqlite3.connect(database=active_db, check_same_thread=False)
    checkpointer = SqliteSaver(conn=conn)

    app = workflow.compile(checkpointer=checkpointer)
    return app


# Default singleton app instance
refund_app = create_refund_workflow()


# ==============================================================================
# 6. HELPER FUNCTIONS FOR APP USAGE & THREAD INSPECTION
# ==============================================================================

def run_refund_investigation(
    complaint_text: str,
    thread_id: str,
    app: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Convenience runner to invoke or resume the refund workflow.
    """
    active_app = app or refund_app
    config = {
        "configurable": {"thread_id": thread_id},
        "metadata": {"thread_id": thread_id, "project": "bharattrip-refund-ops"},
        "run_name": "bharattrip_refund_investigation",
        "tags": ["refund-agent", "groq", "operations"],
    }

    initial_input = {
        "messages": [HumanMessage(content=complaint_text)],
        "complaint_text": complaint_text,
        "investigation_thread_id": thread_id,
    }

    return active_app.invoke(initial_input, config=config)


def resume_with_human_decision(
    decision: str = "SUBMIT_TICKET_TO_FINANCE",
    thread_id: str = "default",
    app: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Resumes an interrupted workflow checkpoint with the human supervisor's decision / audit note.
    Standardized default decision string: 'SUBMIT_TICKET_TO_FINANCE'.
    Note: The decision is recorded strictly as an internal audit note and does not disburse funds.
    """
    active_app = app or refund_app
    config = {
        "configurable": {"thread_id": thread_id},
        "metadata": {"thread_id": thread_id, "project": "bharattrip-refund-ops"},
        "run_name": "bharattrip_human_decision_resume",
        "tags": ["refund-agent", "human-in-the-loop"],
    }

    return active_app.invoke(Command(resume=decision), config=config)

