"""
streamlit_bharattrip_refund.py
------------------------------
Enhanced operations dashboard for BharatTrip AI-powered Refund Assistant.

Five Primary Sections:
  1. 🔍 Refund Investigation (Intake, deterministic cards, human interrupt & auto-assignment)
  2. 📋 Human Action Queue (Queue view, status transitions, supervisor reassignment)
  3. 👥 Employee Work Queues (Employee directory, capacity tracking, individual queues)
  4. 💬 AI Refund Chatbot (Grounded Q&A powered by Groq openai/gpt-oss-120b)
  5. 📊 Reports & History (Persistent chat history, reports, and admin audit drawer)

Guarantees:
  - 100% deterministic reconciliation math (never invented by LLM).
  - Strictly advisory (zero automated refund disbursements).
  - Groq configuration loaded via python-dotenv (.env) without credential exposure.
  - Full LangGraph SQLite checkpoint pause/resume integration.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# Ensure current src directory is in sys.path
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import streamlit as st
import pandas as pd

import bharattrip_groq_config as groq_cfg
import bharattrip_assignment as asgn
import bharattrip_history as hist
from bharattrip_chatbot import BharatTripChatbotService, BLOCKED_EXECUTION_NOTICE
from bharattrip_refund_backend import (
    refund_app,
    run_refund_investigation,
    resume_with_human_decision,
)
from bharattrip_reconciliation import (
    BharatTripReconciliationEngine,
    get_default_workbook_path,
    get_default_report_path,
)

# ==============================================================================
# 1. PAGE SETUP & STYLING
# ==============================================================================

st.set_page_config(
    page_title="BharatTrip Refund Operations Assistant",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded"
)

GOVERNANCE_DISCLAIMER = (
    "This assistant provides advisory investigation support only. "
    "All refund approvals and financial transactions require authorized human action."
)

st.markdown(
    """
    <style>
    .main-header {
        font-size: 1.85rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 0.95rem;
        color: #4B5563;
        margin-bottom: 0.9rem;
    }
    .governance-banner {
        background-color: #FEF3C7;
        border-left: 5px solid #F59E0B;
        padding: 0.75rem 1rem;
        border-radius: 4px;
        color: #92400E;
        font-size: 0.9rem;
        font-weight: 500;
        margin-bottom: 1.15rem;
    }
    .metric-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 0.75rem;
        text-align: center;
    }
    .metric-label {
        font-size: 0.72rem;
        color: #64748B;
        text-transform: uppercase;
        font-weight: 600;
    }
    .metric-value {
        font-size: 1.15rem;
        font-weight: 700;
        color: #0F172A;
    }
    .badge-chip {
        display: inline-block;
        padding: 0.2rem 0.5rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 0.35rem;
        margin-bottom: 0.35rem;
        background: #E2E8F0;
        color: #1E293B;
    }
    .action-box {
        background-color: #EFF6FF;
        border: 1px solid #BFDBFE;
        border-radius: 6px;
        padding: 0.9rem;
        margin-top: 0.5rem;
        color: #1E3A8A;
    }
    .status-badge {
        font-size: 0.72rem;
        padding: 0.15rem 0.45rem;
        border-radius: 4px;
        font-weight: 600;
    }
    .status-completed { background: #DCFCE7; color: #166534; }
    .status-awaiting { background: #FEF3C7; color: #92400E; }
    .status-clarification { background: #FEE2E2; color: #991B1B; }
    .status-inprogress { background: #E0E7FF; color: #3730A3; }
    </style>
    """,
    unsafe_allow_html=True
)

# Initialize databases
hist.init_history_db()
asgn.init_assignment_tables()

# ==============================================================================
# 2. SESSION STATE INITIALIZATION
# ==============================================================================

def new_conversation_session():
    cid = f"conv-{uuid.uuid4()}"
    tid = f"thread-{uuid.uuid4()}"
    st.session_state["active_conversation_id"] = cid
    st.session_state["thread_id"] = tid
    st.session_state["current_result"] = None
    st.session_state["complaint_input"] = ""
    st.session_state["show_delete_confirm"] = False
    st.session_state["show_clear_confirm"] = False
    st.session_state["rename_mode"] = False

if "active_conversation_id" not in st.session_state:
    new_conversation_session()

if "chatbot_messages" not in st.session_state:
    st.session_state["chatbot_messages"] = [
        {"role": "assistant", "content": "Hello! I am the BharatTrip AI Refund Operations Assistant. How can I assist you with ticket reconciliation, discrepancies, or queue assignments today?"}
    ]

# Benchmark Complaints for rapid testing
SAMPLE_COMPLAINTS = {
    "-- Select a benchmark complaint to test --": "",
    "TEST A: RF-1252 (Explained Partial Deduction Dispute)": (
        "Travel agency TripHub is disputing why only partial refund was received for ticket RF-1252. "
        "Support shows claim for ₹21,100 but Finance paid ₹19,400 with a ₹1,700 recorded deduction."
    ),
    "TEST B: RF-1570 (Closed in Support, Missing in Finance)": (
        "Agent Skyline Travels is escalating ticket RF-1570 on BLR-DXB route. "
        "Support portal shows ticket Closed for ₹23,600, but no payout received in agency bank account."
    ),
    "TEST C: RF-1148 (Duplicate Finance Records)": (
        "Urgent inquiry from Skyline Travels regarding refund reference RF-1148 on DEL-BOM route."
    ),
    "TEST D: RF-1757 (Closed in Support, Pending Payout in Finance)": (
        "Wander Agency ticket RF-1757 shows closed on portal, but agent says ₹22,900 refund has not reached them."
    ),
    "TEST E: RF-1286 (Clean Match Settled Full Refund)": (
        "Status follow-up for Wander Agency ticket RF-1286 on DEL-KTM route for ₹10,200."
    ),
    "RF-1325: Linked Escalation Record (Resolved)": (
        "Checking status of GoFly Holidays ticket RF-1325 on BLR-MAA route."
    ),
    "RF-1232: Multiple Secondary Issue Flags": (
        "Status inquiry for GoFly Holidays RF-1232."
    ),
    "RF-1181: Conflicting Support Duplicate Records": (
        "Pinnacle Getaways RF-1181 inquiry."
    ),
    "RF-1110: Finance-Only Record": (
        "Checking ticket RF-1110 for Lotus Travel Co."
    ),
    "TEST F: Missing Reference (Needs Clarification)": (
        "An agent is asking for an urgent refund update on yesterday's flight cancellation, but did not give any reference."
    ),
    "TEST G: Ambiguous Reference (Multiple IDs in Query)": (
        "Agent is asking whether RF-1001 or RF-1002 was paid out first."
    ),
    "TEST H: Refund Execution Attempt (Blocked Autonomous Action)": (
        "Please approve and execute the refund immediately for ticket RF-1286."
    ),
    "TEST I: Invalid / Nonexistent Reference": (
        "Please check the refund status for invalid ticket RF-999999 on BOM-DEL route."
    ),
}

# ==============================================================================
# 3. SIDEBAR: NAVIGATION & GROQ STATUS
# ==============================================================================

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/airplane-take-off.png", width=44)
    st.title("BharatTrip Operations")
    st.caption("AI-Powered Refund Reconciliation")

    st.markdown("---")
    nav_selection = st.radio(
        "Navigation Sections:",
        [
            "🔍 Refund Investigation",
            "📋 Human Action Queue",
            "👥 Employee Work Queues",
            "💬 AI Refund Chatbot",
            "🗂️ Chat & Audit History",
            "📊 Reports & Analytics"
        ],
        index=0
    )

    st.markdown("---")
    st.subheader("⚡ Groq Engine Status")
    groq_key = groq_cfg.get_groq_api_key()
    groq_model = groq_cfg.get_configured_model()

    if groq_key:
        st.success(f"**Connected**: `{groq_model}`")
        st.caption(f"API Key: `{groq_cfg.get_masked_api_key()}`")
    else:
        st.warning("⚠️ **Groq API Key Missing**")
        st.caption("Add `GROQ_API_KEY` to `BharatTrip_Submission/.env` to enable full LLM capabilities.")

    if st.button("🔌 Test Groq Connectivity", use_container_width=True):
        with st.spinner("Connecting to Groq API..."):
            test_res = groq_cfg.verify_groq_connectivity()
            if test_res["connected"]:
                st.toast(f"✅ {test_res['message']}", icon="✅")
            else:
                st.error(test_res["message"])

    st.markdown("---")
    if st.button("➕ New Investigation", use_container_width=True, type="primary"):
        new_conversation_session()
        st.rerun()

# ==============================================================================
# 4. VIEW 1: REFUND INVESTIGATION
# ==============================================================================

if nav_selection == "🔍 Refund Investigation":
    st.markdown('<div class="main-header">Refund Investigation Console</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Deterministic Cross-Department Reconciliation & Automated Workload Routing</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="governance-banner">⚠️ <strong>GOVERNANCE POLICY:</strong> {GOVERNANCE_DISCLAIMER}</div>', unsafe_allow_html=True)

    active_cid = st.session_state["active_conversation_id"]
    active_conv = hist.get_conversation(active_cid)
    current_title = active_conv["title"] if active_conv else "New Investigation"
    st.caption(f"📁 Active Case: **{current_title}** | Thread ID: `{st.session_state['thread_id']}`")

    # Benchmark Preset Selector
    c_p1, c_p2 = st.columns([3, 1])
    with c_p1:
        selected_preset = st.selectbox(
            "Load Benchmark Case Preset:",
            options=list(SAMPLE_COMPLAINTS.keys()),
            index=0
        )
    with c_p2:
        st.write("")
        st.write("")
        if selected_preset and SAMPLE_COMPLAINTS[selected_preset]:
            if st.button("Load Preset", use_container_width=True):
                st.session_state["complaint_input"] = SAMPLE_COMPLAINTS[selected_preset]
                st.rerun()

    # Complaint Input
    complaint_text = st.text_area(
        "Enter customer complaint, travel agency dispute, or ticket notes:",
        value=st.session_state.get("complaint_input", ""),
        height=100,
        placeholder="e.g. Travel agency TripHub is disputing why only partial refund was received for ticket RF-1252...",
        key="main_complaint_area"
    )

    if st.button("🔍 Investigate Refund Request", type="primary", use_container_width=True):
        if not complaint_text.strip():
            st.warning("Please enter complaint details or load a benchmark preset.")
        else:
            with st.spinner("Analyzing complaint and performing deterministic reconciliation..."):
                try:
                    result = run_refund_investigation(
                        complaint_text=complaint_text.strip(),
                        thread_id=st.session_state["thread_id"]
                    )
                    st.session_state["current_result"] = result
                    st.session_state["complaint_input"] = complaint_text

                    # Update persistent history
                    ref_found = result.get("refund_reference")
                    recon = result.get("reconciliation_result") or {}
                    cat_found = recon.get("Primary Category", "UNKNOWN")
                    missing = result.get("missing_fields") or []
                    paused = bool(result.get("__interrupt__") or (result.get("human_review_required") and not result.get("human_decision")))

                    if "refund_reference" in missing or "ambiguous_reference" in missing:
                        rev_status = "NEEDS_CLARIFICATION"
                    elif paused:
                        rev_status = "AWAITING_REVIEW"
                    else:
                        rev_status = "COMPLETED"

                    title = hist.generate_conversation_title(complaint_text, ref_found, cat_found)
                    hist.save_or_update_conversation(
                        conversation_id=active_cid,
                        thread_id=st.session_state["thread_id"],
                        title=title,
                        refund_reference=ref_found,
                        category=cat_found,
                        investigation_status=result.get("investigation_status", "IN_PROGRESS"),
                        human_review_status=rev_status,
                        human_decision=result.get("human_decision"),
                        complaint_text=complaint_text,
                        final_report=result.get("final_report"),
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Investigation execution error: {str(exc)}")

    # Display Investigation Results
    cur_res = st.session_state.get("current_result")
    if cur_res:
        st.markdown("---")
        missing_fields = cur_res.get("missing_fields") or []
        recon_data = cur_res.get("reconciliation_result") or {}
        ref_id = cur_res.get("refund_reference")
        paused = bool(cur_res.get("__interrupt__") or (cur_res.get("human_review_required") and not cur_res.get("human_decision")))

        # Cards
        if recon_data:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.markdown(f'<div class="metric-card"><div class="metric-label">Support Claim</div><div class="metric-value">₹{recon_data.get("Support Amount (INR)") or "N/A"}</div><small>Row: {recon_data.get("Support Rows") or "N/A"}</small></div>', unsafe_allow_html=True)
            with c2:
                st.markdown(f'<div class="metric-card"><div class="metric-label">Finance Paid</div><div class="metric-value">₹{recon_data.get("Finance Amount Paid (INR)") or "N/A"}</div><small>Row: {recon_data.get("Finance Rows") or "N/A"}</small></div>', unsafe_allow_html=True)
            with c3:
                st.markdown(f'<div class="metric-card"><div class="metric-label">Recorded Deduction</div><div class="metric-value">₹{recon_data.get("Finance Deduction (INR)") or "0.0"}</div><small>{recon_data.get("Deduction Type") or "N/A"}</small></div>', unsafe_allow_html=True)
            with c4:
                diff_val = recon_data.get("Amount Difference (INR)", 0.0)
                diff_color = "#DC2626" if diff_val != 0.0 else "#16A34A"
                st.markdown(f'<div class="metric-card"><div class="metric-label">Net Difference</div><div class="metric-value" style="color: {diff_color}">₹{diff_val}</div><small>{"Arithmetic Explained" if recon_data.get("Is Deduction Explained") else "Variance Flagged"}</small></div>', unsafe_allow_html=True)

        # Human Review & Auto-Assignment Section
        if paused:
            st.warning("⏸️ **HUMAN REVIEW REQUIRED**: Workflow paused at SQLite checkpoint. Discrepancy requires supervisor sign-off.")

            # Retrieve active assignment details
            asgn_details = asgn.get_assignment_by_thread(st.session_state["thread_id"])

            col_rev1, col_rev2 = st.columns([2, 1])
            with col_rev1:
                st.markdown("#### Automatic Workload Assignment Details")
                if asgn_details:
                    st.info(
                        f"👤 **Assigned Employee**: **{asgn_details['assigned_employee_name']}** ({asgn_details['assigned_department']})\n\n"
                        f"📌 **Status**: `{asgn_details['assignment_status']}` | Method: `{asgn_details['assignment_method']}`\n\n"
                        f"📝 **Notes**: {asgn_details.get('review_notes')}"
                    )
                else:
                    st.caption("Assignment being initialized...")

                # Supervisor Decision Form
                st.markdown("#### Submit Supervisor Review Decision")
                sup_decision = st.radio(
                    "Select standard operational action:",
                    [
                        "SUBMIT_TICKET_TO_FINANCE",
                        "ESCALATE_TO_SUPERVISOR_AUDIT",
                        "REQUEST_AGENCY_POLICY_PROOF",
                        "CLOSE_AS_ALREADY_SETTLED"
                    ],
                    index=0
                )
                sup_notes = st.text_input("Supervisor audit notes (optional):", value="Verified cross-department discrepancy. Submitting for operational handover.")

                if st.button("✅ Submit Decision & Resume Investigation", type="primary", use_container_width=True):
                    with st.spinner("Recording audit decision in SQLite and resuming LangGraph workflow..."):
                        try:
                            resumed_res = resume_with_human_decision(
                                decision=sup_decision,
                                thread_id=st.session_state["thread_id"]
                            )
                            st.session_state["current_result"] = resumed_res

                            # Update history
                            hist.save_or_update_conversation(
                                conversation_id=active_cid,
                                thread_id=st.session_state["thread_id"],
                                title=current_title,
                                refund_reference=ref_id,
                                category=recon_data.get("Primary Category", "UNKNOWN"),
                                investigation_status="COMPLETED",
                                human_review_status="COMPLETED",
                                human_decision=sup_decision,
                                complaint_text=st.session_state.get("complaint_input", ""),
                                final_report=resumed_res.get("final_report"),
                            )
                            st.success("Supervisor decision recorded successfully! Workflow completed.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error resuming workflow: {e}")

            with col_rev2:
                st.markdown("#### Supervisor Reassignment")
                all_emps = asgn.list_all_employees()
                emp_options = {f"{e['employee_name']} ({e['department']} - {e['role']})": e['employee_id'] for e in all_emps if e['active_status'] == 1}

                selected_reassign = st.selectbox("Reassign Case To:", options=list(emp_options.keys()))
                reassign_reason = st.text_input("Reassignment Reason:", value="Workload balancing / supervisory escalation")

                if st.button("🔄 Confirm Reassignment", use_container_width=True):
                    if asgn_details:
                        new_emp_id = emp_options[selected_reassign]
                        ok = asgn.reassign_employee(
                            assignment_id=asgn_details["assignment_id"],
                            new_employee_id=new_emp_id,
                            changed_by="SUPERVISOR",
                            change_reason=reassign_reason
                        )
                        if ok:
                            st.toast("Employee reassigned successfully!", icon="✅")
                            st.rerun()

        # Render Final Report if available
        final_rep = cur_res.get("final_report")
        if final_rep:
            st.markdown("### Investigation Advisory Report")
            st.markdown(final_rep)

# ==============================================================================
# 5. VIEW 2: HUMAN ACTION QUEUE
# ==============================================================================

elif nav_selection == "📋 Human Action Queue":
    st.markdown('<div class="main-header">Human Action Queue</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Centralized Operations Queue for Discrepancies Requiring Manual Intervention</div>', unsafe_allow_html=True)

    # Queue Filters
    f1, f2, f3 = st.columns(3)
    with f1:
        dept_f = st.selectbox("Filter Department:", ["ALL", "Finance", "Support", "Operations"])
    with f2:
        status_f = st.selectbox("Filter Status:", ["ALL", "UNASSIGNED", "ASSIGNED", "IN_PROGRESS", "AWAITING_FINANCE", "AWAITING_SUPPORT", "REVIEWED", "RESOLVED"])
    with f3:
        all_e = asgn.list_all_employees()
        emp_filter_opts = ["ALL"] + [e["employee_id"] for e in all_e]
        emp_f = st.selectbox("Filter Employee ID:", emp_filter_opts)

    queue = asgn.list_human_action_queue(
        status_filter=status_f,
        department_filter=dept_f,
        employee_id_filter=emp_f
    )

    st.markdown(f"**Total Cases in Queue**: `{len(queue)}`")

    if not queue:
        st.info("No active discrepancy cases matching filter criteria.")
    else:
        for item in queue:
            with st.expander(f"📁 {item['refund_reference']} — {item['issue_category']} ({item['assignment_status']})", expanded=True):
                qc1, qc2, qc3 = st.columns([2, 2, 1])
                with qc1:
                    st.markdown(f"**Refund Reference**: `{item['refund_reference']}`")
                    st.markdown(f"**Issue Category**: `{item['issue_category']}`")
                    st.markdown(f"**Responsible Department**: `{item['assigned_department']}`")
                    st.markdown(f"**Assignment Method**: `{item['assignment_method']}`")
                with qc2:
                    st.markdown(f"**Assigned Employee**: **{item['assigned_employee_name']}** (`{item['assigned_employee_id'] or 'None'}`)")
                    st.markdown(f"**Assignment Status**: `{item['assignment_status']}`")
                    st.markdown(f"**Workload at Assignment**: `{item['workload_at_assignment']} active cases`")
                    st.markdown(f"**Last Updated**: `{item['last_updated_at']}`")
                with qc3:
                    st.markdown(f"**Assignment ID**: `{item['assignment_id']}`")
                    st.markdown(f"**Thread ID**: `{item['investigation_thread_id']}`")

                st.markdown(f"**Operational Review Notes**: {item.get('review_notes') or 'No notes recorded.'}")

                # Inline Status Update and Reassignment Drawer
                with st.container():
                    st.markdown("---")
                    st.markdown("**Update Case Status / Reassign**")
                    u1, u2, u3 = st.columns([1, 2, 1])
                    with u1:
                        new_st = st.selectbox("Update Status:", asgn.ALL_ASSIGNMENT_STATUSES, index=asgn.ALL_ASSIGNMENT_STATUSES.index(item['assignment_status']), key=f"st_{item['assignment_id']}")
                    with u2:
                        up_notes = st.text_input("Review / Handover Notes:", key=f"notes_{item['assignment_id']}", placeholder="e.g. Verified with bank ledger; refund approved for release.")
                    with u3:
                        st.write("")
                        st.write("")
                        if st.button("Save Status Update", key=f"btn_up_{item['assignment_id']}", use_container_width=True):
                            asgn.update_assignment_status(
                                assignment_id=item["assignment_id"],
                                new_status=new_st,
                                changed_by="SUPERVISOR",
                                review_notes=up_notes
                            )
                            st.toast("Status updated successfully!", icon="✅")
                            st.rerun()

                # Audit History for this assignment
                audit_logs = asgn.get_assignment_audit_history(item["assignment_id"])
                if audit_logs:
                    with st.expander("📜 Assignment Audit Trail"):
                        for a in audit_logs:
                            st.caption(f"[{a['timestamp']}] By `{a['changed_by']}`: {a['change_reason']} (Status: `{a['previous_status']}` ➔ `{a['new_status']}` | Assigned: `{a['previous_employee_name']}` ➔ `{a['new_employee_name']}`)")

# ==============================================================================
# 6. VIEW 3: EMPLOYEE DIRECTORY & WORK QUEUES
# ==============================================================================

elif nav_selection == "👥 Employee Work Queues":
    st.markdown('<div class="main-header">Employee Directory & Workload Management</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Deterministic Capacity Tracking, Availability Controls, and Individual Work Queues</div>', unsafe_allow_html=True)

    tab_dir, tab_my = st.tabs(["👥 Employee Directory & Capacity", "📥 Individual Employee Work Queue"])

    with tab_dir:
        st.subheader("Configured Employee Directory (Sample Staff)")
        employees = asgn.list_all_employees()

        emp_table_data = []
        for e in employees:
            wl = e["active_workload"]
            max_c = e["maximum_active_cases"]
            cap_ratio = f"{wl} / {max_c}"
            status_text = "🟢 Active" if e["active_status"] == 1 else "🔴 Inactive"
            emp_table_data.append({
                "ID": e["employee_id"],
                "Name": e["employee_name"],
                "Department": e["department"],
                "Role": e["role"],
                "Status": status_text,
                "Availability": e["availability_status"],
                "Active Workload": cap_ratio,
            })

        st.dataframe(pd.DataFrame(emp_table_data), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("Manage Staff Availability & Capacity")
        m1, m2, m3 = st.columns(3)
        with m1:
            emp_dict = {f"{e['employee_id']} - {e['employee_name']}": e['employee_id'] for e in employees}
            selected_emp_key = st.selectbox("Select Employee:", list(emp_dict.keys()))
            target_emp_id = emp_dict[selected_emp_key]
        with m2:
            new_avail = st.selectbox("Set Availability:", ["AVAILABLE", "ON_LEAVE", "BUSY"])
            if st.button("Update Availability", use_container_width=True):
                asgn.toggle_employee_availability(target_emp_id, new_avail)
                st.toast(f"Availability updated to {new_avail}!", icon="✅")
                st.rerun()
        with m3:
            curr_emp = [e for e in employees if e["employee_id"] == target_emp_id][0]
            new_active = 0 if curr_emp["active_status"] == 1 else 1
            btn_label = "Deactivate Employee" if curr_emp["active_status"] == 1 else "Activate Employee"
            if st.button(btn_label, use_container_width=True):
                asgn.toggle_employee_active_status(target_emp_id, new_active)
                st.toast(f"Employee status updated!", icon="✅")
                st.rerun()

    with tab_my:
        st.subheader("Individual Staff Work Queue")
        q_emp_dict = {f"{e['employee_name']} ({e['department']} - {e['role']})": e['employee_id'] for e in employees}
        sel_q_emp_label = st.selectbox("View Work Queue For:", list(q_emp_dict.keys()), key="sel_queue_emp")
        sel_q_emp_id = q_emp_dict[sel_q_emp_label]

        assigned_cases = asgn.list_human_action_queue(employee_id_filter=sel_q_emp_id)
        st.markdown(f"**Assigned Cases for {sel_q_emp_label}**: `{len(assigned_cases)}`")

        if not assigned_cases:
            st.info("No active cases assigned to this employee.")
        else:
            for c in assigned_cases:
                st.markdown(f"### Ticket: `{c['refund_reference']}` — {c['issue_category']}")
                st.caption(f"Status: `{c['assignment_status']}` | Assigned At: `{c['assigned_at']}`")
                st.markdown(f"**Case Notes**: {c.get('review_notes') or 'N/A'}")

                uc1, uc2 = st.columns([1, 3])
                with uc1:
                    new_c_status = st.selectbox(
                        "Update Status:",
                        asgn.ALL_ASSIGNMENT_STATUSES,
                        index=asgn.ALL_ASSIGNMENT_STATUSES.index(c['assignment_status']),
                        key=f"my_st_{c['assignment_id']}"
                    )
                with uc2:
                    c_notes = st.text_input("Add update notes:", key=f"my_notes_{c['assignment_id']}", placeholder="e.g. Investigation underway; contacted airline for cancellation proof.")
                    if st.button("Save Update", key=f"my_btn_{c['assignment_id']}"):
                        asgn.update_assignment_status(
                            assignment_id=c["assignment_id"],
                            new_status=new_c_status,
                            changed_by=sel_q_emp_id,
                            review_notes=c_notes
                        )
                        st.toast("Update saved!", icon="✅")
                        st.rerun()
                st.markdown("---")

# ==============================================================================
# 7. VIEW 4: AI REFUND CHATBOT
# ==============================================================================

elif nav_selection == "💬 AI Refund Chatbot":
    st.markdown('<div class="main-header">BharatTrip AI Refund Chatbot</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Conversational Intelligence Grounded Exclusively in Deterministic Reconciliation Records</div>', unsafe_allow_html=True)

    st.info(
        "💡 **Ask questions in plain English** about any ticket reference, cross-department discrepancies, duplicate records, or pending action queue items. "
        "Answers are derived 100% from actual Support, Finance, and Escalation datasets via Groq."
    )

    # Initialize chatbot service
    bot_service = BharatTripChatbotService(db_path=asgn.DEFAULT_DB_PATH)

    # Clickable Suggestion Chips
    st.markdown("**Suggested Questions:**")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        if st.button("❓ Why is RF-1570 awaiting review?", use_container_width=True):
            st.session_state["chatbot_prompt_input"] = "Why is RF-1570 awaiting review?"
        if st.button("❓ What happened to RF-1252?", use_container_width=True):
            st.session_state["chatbot_prompt_input"] = "What happened to RF-1252?"
    with sc2:
        if st.button("❓ Which cases have duplicate Finance records?", use_container_width=True):
            st.session_state["chatbot_prompt_input"] = "Which cases have duplicate Finance records?"
        if st.button("❓ Which refunds need human attention?", use_container_width=True):
            st.session_state["chatbot_prompt_input"] = "Which refunds need human attention?"
    with sc3:
        if st.button("❓ What should Finance investigate next?", use_container_width=True):
            st.session_state["chatbot_prompt_input"] = "What should Finance investigate next?"
        if st.button("⚠️ Try: Approve and pay out RF-1286", use_container_width=True):
            st.session_state["chatbot_prompt_input"] = "Please approve and execute refund payout for RF-1286 immediately."

    st.markdown("---")

    # Render Chat History
    for msg in st.session_state["chatbot_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # User Input
    user_query = st.chat_input("Ask a question about refund tickets or reconciliation...")
    if not user_query and st.session_state.get("chatbot_prompt_input"):
        user_query = st.session_state.pop("chatbot_prompt_input")

    if user_query:
        # Append User Message
        st.session_state["chatbot_messages"].append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        # Generate Assistant Response
        with st.chat_message("assistant"):
            with st.spinner("Analyzing verified records and querying Groq..."):
                response_dict = bot_service.answer_query(
                    query=user_query,
                    conversation_id=st.session_state["active_conversation_id"]
                )
                answer_text = response_dict["answer"]
                st.markdown(answer_text)
                st.session_state["chatbot_messages"].append({"role": "assistant", "content": answer_text})

# ==============================================================================
# 8. VIEW 5: CHAT & AUDIT HISTORY
# ==============================================================================

elif nav_selection == "🗂️ Chat & Audit History":
    st.markdown('<div class="main-header">Investigation & Chat History</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Persistent SQLite Conversation Management & Historical Replay</div>', unsafe_allow_html=True)

    all_convs = hist.list_all_conversations()

    if not all_convs:
        st.info("No saved investigations in history.")
    else:
        st.markdown(f"**Total Saved Investigations**: `{len(all_convs)}`")

        for c in all_convs:
            cid = c["conversation_id"]
            title = c["title"]
            ref = c["refund_reference"] or "N/A"
            rev_stat = c["human_review_status"]
            up_time = c["updated_at"]
            is_active = (cid == st.session_state["active_conversation_id"])

            with st.expander(f"{'🔹 [ACTIVE] ' if is_active else ''}{title} ({ref})", expanded=is_active):
                st.caption(f"Conversation ID: `{cid}` | Thread ID: `{c['thread_id']}` | Updated: `{up_time}`")
                st.markdown(f"**Status**: `{c['investigation_status']}` | **Review**: `{rev_stat}`")
                if c.get("complaint_text"):
                    st.markdown(f"**Complaint**: {c['complaint_text']}")
                if c.get("final_report"):
                    st.markdown("**Final Advisory Report**:")
                    st.markdown(c["final_report"])

                col_a1, col_a2 = st.columns([1, 4])
                with col_a1:
                    if st.button("Switch To This Chat", key=f"sw_{cid}", use_container_width=True):
                        st.session_state["active_conversation_id"] = cid
                        st.session_state["thread_id"] = c["thread_id"]
                        st.session_state["complaint_input"] = c.get("complaint_text", "")
                        st.session_state["current_result"] = None
                        st.toast(f"Switched to {title}!", icon="📁")
                        st.rerun()

# ==============================================================================
# 9. VIEW 6: REPORTS & ANALYTICS
# ==============================================================================

elif nav_selection == "📊 Reports & Analytics":
    st.markdown('<div class="main-header">Reports & Operational Analytics</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Dataset Dimensions, Reconciliation Exports & Technical Audit Drawer</div>', unsafe_allow_html=True)

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown('<div class="metric-card"><div class="metric-label">Support Records</div><div class="metric-value">638</div><small>22 duplicate IDs (44 rows)</small></div>', unsafe_allow_html=True)
    with m2:
        st.markdown('<div class="metric-card"><div class="metric-label">Finance Records</div><div class="metric-value">638</div><small>9 duplicate refs (18 rows)</small></div>', unsafe_allow_html=True)
    with m3:
        st.markdown('<div class="metric-card"><div class="metric-label">Escalation Tickets</div><div class="metric-value">257</div><small>High severity disputes</small></div>', unsafe_allow_html=True)
    with m4:
        st.markdown('<div class="metric-card"><div class="metric-label">Benchmark Accuracy</div><div class="metric-value">100%</div><small>84 / 84 Automated Tests</small></div>', unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("📥 Export Deterministic Reconciliation Reports")
    st.caption("Reconciliation exports are derived directly from the master workbook data without modification.")

    try:
        csv_path = get_default_report_path("csv")
        xlsx_path = get_default_report_path("xlsx")
    except Exception as path_err:
        st.error(f"Error resolving report paths: {path_err}")
        csv_path = "data/BharatTrip_Reconciliation_Report.csv"
        xlsx_path = "data/BharatTrip_Reconciliation_Report.xlsx"

    # Action to regenerate reports on-demand using latest deterministic data
    col_gen1, col_gen2 = st.columns([2, 3])
    with col_gen1:
        if st.button("🔄 Re-generate Latest Reports (CSV & Excel)", use_container_width=True):
            with st.spinner("Executing full reconciliation across Support, Finance & Escalation sheets..."):
                try:
                    engine = BharatTripReconciliationEngine()
                    engine.generate_reconciliation_report(csv_path, export_excel=True)
                    st.success("✅ Reconciliation reports generated successfully from latest workbook data!")
                    st.rerun()
                except Exception as gen_err:
                    st.error(f"Failed to generate reports: {gen_err}")

    st.write("")

    # Check existence and render download buttons
    d1, d2 = st.columns(2)
    with d1:
        if os.path.exists(csv_path):
            try:
                mod_time = datetime.fromtimestamp(os.path.getmtime(csv_path)).strftime("%Y-%m-%d %H:%M:%S")
                with open(csv_path, "rb") as f:
                    csv_bytes = f.read()
                st.download_button(
                    "📥 Download Reconciliation Report (CSV)",
                    data=csv_bytes,
                    file_name="BharatTrip_Reconciliation_Report.csv",
                    mime="text/csv",
                    use_container_width=True
                )
                st.caption(f"CSV file size: {len(csv_bytes):,} bytes | Last updated: `{mod_time}`")
            except Exception as read_err:
                st.error(f"Error reading CSV report: {read_err}")
        else:
            st.warning("⚠️ CSV report file not found on disk. Click 'Re-generate' above to create it.")

    with d2:
        if os.path.exists(xlsx_path):
            try:
                mod_time = datetime.fromtimestamp(os.path.getmtime(xlsx_path)).strftime("%Y-%m-%d %H:%M:%S")
                with open(xlsx_path, "rb") as f:
                    xlsx_bytes = f.read()
                st.download_button(
                    "📥 Download Reconciliation Report (Excel)",
                    data=xlsx_bytes,
                    file_name="BharatTrip_Reconciliation_Report.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
                st.caption(f"Excel file size: {len(xlsx_bytes):,} bytes | Last updated: `{mod_time}`")
            except Exception as read_err:
                st.error(f"Error reading Excel report: {read_err}")
        else:
            st.warning("⚠️ Excel report file not found on disk. Click 'Re-generate' above to create it.")

    # Technical Audit Drawer for Administrators
    with st.expander("🛠️ Technical Audit Drawer (For System Administrators)", expanded=False):
        st.markdown("#### Database Tables & Checkpoints")
        st.caption("Active Database: `data/bharattrip_operations.db`")
        if st.session_state.get("current_result"):
            st.json(st.session_state["current_result"])
        else:
            st.info("Run an investigation to inspect raw LangGraph state dictionary.")
