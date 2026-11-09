"""
bharattrip_assignment.py
-------------------------
Automatic workload-based human assignment and employee management system
for BharatTrip Refund Operations Assistant.

Guarantees:
- Deterministic department and role routing based on reconciliation issue flags.
- Workload-based employee selection with round-robin tie breaking.
- Strict enforcement of active capacity limits and availability statuses.
- Atomic SQLite transactions preventing concurrent over-assignment.
- Complete, immutable audit log for assignments, reassignments, and status changes.
- Fictional sample employees clearly labeled as sample records.
- Zero autonomous financial execution or approvals.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def get_default_db_path() -> str:
    """Resolves operations SQLite database in package data/ directory or CWD."""
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_data = os.path.join(pkg_root, "data")
    if os.path.exists(target_data):
        return os.path.join(target_data, "bharattrip_operations.db")
    return "bharattrip_operations.db"


DEFAULT_DB_PATH = get_default_db_path()

# Valid assignment statuses
ACTIVE_WORKLOAD_STATUSES = (
    "ASSIGNED",
    "IN_PROGRESS",
    "AWAITING_FINANCE",
    "AWAITING_SUPPORT",
    "REVIEWED",
)
ALL_ASSIGNMENT_STATUSES = (
    "UNASSIGNED",
    "ASSIGNED",
    "IN_PROGRESS",
    "AWAITING_FINANCE",
    "AWAITING_SUPPORT",
    "REVIEWED",
    "RESOLVED",
)

# Sample fictional employee seed directory
SAMPLE_EMPLOYEES = [
    {
        "employee_id": "EMP-FIN-01",
        "employee_name": "Priya Sharma (Sample)",
        "department": "Finance",
        "role": "Analyst",
        "active_status": 1,
        "availability_status": "AVAILABLE",
        "maximum_active_cases": 5,
    },
    {
        "employee_id": "EMP-FIN-02",
        "employee_name": "Rahul Verma (Sample)",
        "department": "Finance",
        "role": "Supervisor",
        "active_status": 1,
        "availability_status": "AVAILABLE",
        "maximum_active_cases": 4,
    },
    {
        "employee_id": "EMP-FIN-03",
        "employee_name": "Ananya Iyer (Sample)",
        "department": "Finance",
        "role": "Policy Owner",
        "active_status": 1,
        "availability_status": "AVAILABLE",
        "maximum_active_cases": 4,
    },
    {
        "employee_id": "EMP-SUP-01",
        "employee_name": "Amit Patel (Sample)",
        "department": "Support",
        "role": "Supervisor",
        "active_status": 1,
        "availability_status": "AVAILABLE",
        "maximum_active_cases": 5,
    },
    {
        "employee_id": "EMP-SUP-02",
        "employee_name": "Neha Gupta (Sample)",
        "department": "Support",
        "role": "Analyst",
        "active_status": 1,
        "availability_status": "AVAILABLE",
        "maximum_active_cases": 5,
    },
    {
        "employee_id": "EMP-OPS-01",
        "employee_name": "Suresh Menon (Sample)",
        "department": "Operations",
        "role": "Operations Manager",
        "active_status": 1,
        "availability_status": "AVAILABLE",
        "maximum_active_cases": 3,
    },
]


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(database=db_path, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_assignment_tables(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initializes employee and assignment tables, seeding sample employees if empty."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            # 1. Employees Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    employee_id TEXT PRIMARY KEY,
                    employee_name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active_status INTEGER DEFAULT 1,
                    availability_status TEXT DEFAULT 'AVAILABLE',
                    maximum_active_cases INTEGER DEFAULT 5,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # 2. Investigation Assignments Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS investigation_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    refund_reference TEXT NOT NULL,
                    investigation_thread_id TEXT NOT NULL,
                    issue_category TEXT NOT NULL,
                    assigned_department TEXT NOT NULL,
                    assigned_employee_id TEXT,
                    assigned_employee_name TEXT,
                    assignment_method TEXT NOT NULL,
                    workload_at_assignment INTEGER DEFAULT 0,
                    assignment_status TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    last_updated_at TEXT NOT NULL,
                    review_notes TEXT,
                    FOREIGN KEY (assigned_employee_id) REFERENCES employees(employee_id)
                );
            """)

            # 3. Assignment Audit Log Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS assignment_audit_log (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    assignment_id TEXT NOT NULL,
                    refund_reference TEXT NOT NULL,
                    previous_employee_id TEXT,
                    previous_employee_name TEXT,
                    new_employee_id TEXT,
                    new_employee_name TEXT,
                    previous_status TEXT,
                    new_status TEXT,
                    changed_by TEXT NOT NULL,
                    change_reason TEXT,
                    timestamp TEXT NOT NULL
                );
            """)

            # Indexes for high performance querying
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assignments_thread ON investigation_assignments(investigation_thread_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assignments_ref ON investigation_assignments(refund_reference);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assignments_emp ON investigation_assignments(assigned_employee_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assignments_status ON investigation_assignments(assignment_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_asgn ON assignment_audit_log(assignment_id);")

            # Seed sample employees if table is empty
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM employees;")
            count = cur.fetchone()[0]
            if count == 0:
                now_str = datetime.now().isoformat()
                for emp in SAMPLE_EMPLOYEES:
                    conn.execute("""
                        INSERT INTO employees (
                            employee_id, employee_name, department, role,
                            active_status, availability_status, maximum_active_cases,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        emp["employee_id"],
                        emp["employee_name"],
                        emp["department"],
                        emp["role"],
                        emp["active_status"],
                        emp["availability_status"],
                        emp["maximum_active_cases"],
                        now_str,
                        now_str,
                    ))
    finally:
        conn.close()


# ==============================================================================
# 1. DETERMINISTIC DEPARTMENT & ROLE ROUTING
# ==============================================================================

def route_issue_to_department_and_role(
    issue_category: Optional[str],
    issue_flags: Optional[List[str]] = None,
) -> Tuple[str, str, str]:
    """
    Deterministically routes a discrepancy to the responsible department and role.
    Returns: (department, role, rationale)
    """
    flags = [str(f).upper() for f in (issue_flags or [])]
    cat = (issue_category or "").upper()

    # 1. Conflicting Support duplicates -> Support Supervisor
    if "CONFLICTING_SUPPORT_DUPLICATES" in flags or "CONFLICTING_SUPPORT_DUPLICATE" in flags or cat == "CONFLICTING_SUPPORT_DUPLICATE":
        return "Support", "Supervisor", "Conflicting Support ticket claim amounts require Support Supervisor verification."

    # 2. Support duplicate review -> Support Supervisor
    if "SUPPORT_DUPLICATE_REVIEW" in flags or cat == "SUPPORT_DUPLICATE_REVIEW":
        return "Support", "Supervisor", "Duplicate Support tickets detected; requires Support Supervisor audit."

    # 3. Duplicate Finance records -> Finance Supervisor (high double-payment hazard)
    if "DUPLICATE_IN_FINANCE" in flags or "DUPLICATE_FINANCE_RECORDS" in flags or cat == "DUPLICATE_IN_FINANCE":
        return "Finance", "Supervisor", "Multiple Finance payout records detected; requires Finance Supervisor audit to prevent duplicate payout."

    # 4. Missing Finance record -> Finance Analyst
    if "MISSING_IN_FINANCE" in flags or cat == "MISSING_IN_FINANCE":
        return "Finance", "Analyst", "Ticket marked Closed in Support but missing in Finance ledger; requires Finance intake and payout processing."

    # 5. Closed in Support, pending in Finance -> Finance Analyst
    if "CLOSED_BUT_PENDING" in flags or cat == "CLOSED_BUT_PENDING":
        return "Finance", "Analyst", "Finance payout status is PENDING; requires Finance Analyst expediting with payment gateway."

    # 6. Unverified policy or amount difference / deduction dispute -> Policy Owner or Finance Supervisor
    if "UNVERIFIED_POLICY_AUTHORIZATION" in flags or "UNVERIFIED_AMOUNT_DIFFERENCE" in flags or cat == "UNVERIFIED_POLICY_AUTHORIZATION":
        return "Finance", "Policy Owner", "Deduction arithmetic noted but supporting policy authorization is unverified; requires Policy Owner review."

    # 7. Closed in Support but Declined in Finance / Unresolved Escalation -> Operations Manager
    if "CLOSED_BUT_DECLINED" in flags or "ESCALATION_UNRESOLVED" in flags or cat == "CLOSED_BUT_DECLINED":
        return "Operations", "Operations Manager", "High-severity discrepancy (declined payout or unresolved escalation) requires Operations Manager review."

    # 8. Not found anywhere -> Support Supervisor
    if "NOT_FOUND_IN_SUPPORT_OR_FINANCE" in flags:
        return "Support", "Supervisor", "Reference not found in Support or Finance trackers; requires Support verification."

    # 9. Default fallback for other review triggers
    return "Finance", "Supervisor", "Cross-departmental financial variance requires supervisory review."


# ==============================================================================
# 2. WORKLOAD CALCULATION & EMPLOYEE SELECTION
# ==============================================================================

def get_employee_active_workload(employee_id: str, db_path: str = DEFAULT_DB_PATH) -> int:
    """Calculates active assigned cases for an employee. Resolved cases do not count."""
    conn = get_db_connection(db_path)
    try:
        placeholders = ",".join("?" for _ in ACTIVE_WORKLOAD_STATUSES)
        query = f"""
            SELECT COUNT(*) FROM investigation_assignments
            WHERE assigned_employee_id = ?
            AND assignment_status IN ({placeholders});
        """
        params = [employee_id] + list(ACTIVE_WORKLOAD_STATUSES)
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone()[0]
    finally:
        conn.close()


def select_eligible_employee(
    department: str,
    role: str,
    db_path: str = DEFAULT_DB_PATH
) -> Tuple[Optional[Dict[str, Any]], int, str]:
    """
    Selects the best eligible employee using deterministic workload-based assignment.
    Criteria:
      1. Must be active (active_status == 1).
      2. Must be available (availability_status == 'AVAILABLE').
      3. Department and role match (fallback to department match if role has no availability).
      4. Current active workload < maximum_active_cases.
      5. Lowest active workload wins.
      6. Tie breaker: round-robin based on oldest last assignment timestamp, then employee_id.
    Returns: (selected_employee_dict or None, workload_at_assignment, rationale)
    """
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()

        # Step 1: Query active, available employees in target department & role
        cur.execute("""
            SELECT employee_id, employee_name, department, role,
                   active_status, availability_status, maximum_active_cases
            FROM employees
            WHERE department = ? AND role = ? AND active_status = 1 AND availability_status = 'AVAILABLE';
        """, (department, role))
        candidates = [dict(r) for r in cur.fetchall()]

        # Fallback to any active available in department if strict role match has no one
        if not candidates:
            cur.execute("""
                SELECT employee_id, employee_name, department, role,
                       active_status, availability_status, maximum_active_cases
                FROM employees
                WHERE department = ? AND active_status = 1 AND availability_status = 'AVAILABLE';
            """, (department,))
            candidates = [dict(r) for r in cur.fetchall()]

        if not candidates:
            return None, 0, f"No active and available employees found in department '{department}'."

        # Step 2: Compute current active workload for each candidate
        placeholders = ",".join("?" for _ in ACTIVE_WORKLOAD_STATUSES)
        eligible_pool = []

        for emp in candidates:
            emp_id = emp["employee_id"]
            cur.execute(f"""
                SELECT COUNT(*) FROM investigation_assignments
                WHERE assigned_employee_id = ?
                AND assignment_status IN ({placeholders});
            """, [emp_id] + list(ACTIVE_WORKLOAD_STATUSES))
            current_workload = cur.fetchone()[0]

            max_allowed = emp["maximum_active_cases"]
            if current_workload < max_allowed:
                # Query last assignment timestamp for round-robin tie breaking
                cur.execute("""
                    SELECT MAX(assigned_at) FROM investigation_assignments
                    WHERE assigned_employee_id = ?;
                """, (emp_id,))
                last_assigned = cur.fetchone()[0] or ""

                emp["current_workload"] = current_workload
                emp["last_assigned_at"] = last_assigned
                eligible_pool.append(emp)

        if not eligible_pool:
            return None, 0, f"All available employees in department '{department}' have reached maximum active capacity."

        # Step 3: Sort by workload ascending, then last_assigned_at ascending (round-robin), then employee_id
        eligible_pool.sort(key=lambda x: (x["current_workload"], x["last_assigned_at"], x["employee_id"]))
        best = eligible_pool[0]
        workload = best["current_workload"]

        rationale = (
            f"Selected {best['employee_name']} ({best['role']}) with lowest active workload "
            f"({workload}/{best['maximum_active_cases']} cases) via deterministic capacity routing."
        )
        return best, workload, rationale
    finally:
        conn.close()


# ==============================================================================
# 3. ASSIGNMENT EXECUTION & ATOMIC TRANSACTIONS
# ==============================================================================

def assign_investigation_case(
    refund_reference: str,
    investigation_thread_id: str,
    issue_category: str,
    issue_flags: Optional[List[str]] = None,
    review_notes: Optional[str] = None,
    assigned_by: str = "SYSTEM_AUTOMATION",
    db_path: str = DEFAULT_DB_PATH
) -> Dict[str, Any]:
    """
    Assigns or retrieves an existing assignment for an investigation thread atomically.
    Prevents duplicate assignments for the same thread.
    """
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        # Atomic transaction
        conn.execute("BEGIN IMMEDIATE;")

        cur = conn.cursor()
        # Check if already assigned for this thread
        cur.execute("""
            SELECT * FROM investigation_assignments
            WHERE investigation_thread_id = ?;
        """, (investigation_thread_id,))
        existing = cur.fetchone()

        if existing:
            conn.commit()
            return dict(existing)

        # Determine target department and role
        dept, role, routing_reason = route_issue_to_department_and_role(
            issue_category=issue_category,
            issue_flags=issue_flags
        )

        # Select eligible employee
        selected_emp, workload, select_reason = select_eligible_employee(
            department=dept,
            role=role,
            db_path=db_path
        )

        now_str = datetime.now().isoformat()
        assignment_id = f"ASGN-{uuid.uuid4().hex[:8].upper()}"

        if selected_emp:
            emp_id = selected_emp["employee_id"]
            emp_name = selected_emp["employee_name"]
            asgn_method = "AUTOMATIC_WORKLOAD"
            status = "ASSIGNED"
            notes = review_notes or f"Auto-assigned to {emp_name}. {routing_reason}"
        else:
            emp_id = None
            emp_name = "Unassigned"
            asgn_method = "UNASSIGNED_NO_ELIGIBLE"
            status = "UNASSIGNED"
            notes = review_notes or f"ALERT: Case UNASSIGNED. {select_reason}"

        # Insert assignment record
        conn.execute("""
            INSERT INTO investigation_assignments (
                assignment_id, refund_reference, investigation_thread_id,
                issue_category, assigned_department, assigned_employee_id,
                assigned_employee_name, assignment_method, workload_at_assignment,
                assignment_status, assigned_at, last_updated_at, review_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            assignment_id,
            refund_reference,
            investigation_thread_id,
            issue_category,
            dept,
            emp_id,
            emp_name,
            asgn_method,
            workload,
            status,
            now_str,
            now_str,
            notes,
        ))

        # Insert audit log entry
        conn.execute("""
            INSERT INTO assignment_audit_log (
                assignment_id, refund_reference,
                previous_employee_id, previous_employee_name,
                new_employee_id, new_employee_name,
                previous_status, new_status,
                changed_by, change_reason, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            assignment_id,
            refund_reference,
            None,
            None,
            emp_id,
            emp_name,
            None,
            status,
            assigned_by,
            f"Initial case assignment ({asgn_method}): {notes}",
            now_str,
        ))

        conn.commit()

        return {
            "assignment_id": assignment_id,
            "refund_reference": refund_reference,
            "investigation_thread_id": investigation_thread_id,
            "issue_category": issue_category,
            "assigned_department": dept,
            "assigned_employee_id": emp_id,
            "assigned_employee_name": emp_name,
            "assignment_method": asgn_method,
            "workload_at_assignment": workload,
            "assignment_status": status,
            "assigned_at": now_str,
            "last_updated_at": now_str,
            "review_notes": notes,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_assignment_status(
    assignment_id: str,
    new_status: str,
    changed_by: str,
    review_notes: Optional[str] = None,
    change_reason: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """Updates the status of an assignment and appends to the immutable audit log."""
    if new_status not in ALL_ASSIGNMENT_STATUSES:
        raise ValueError(f"Invalid assignment status: {new_status}. Must be one of {ALL_ASSIGNMENT_STATUSES}")

    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM investigation_assignments WHERE assignment_id = ?;", (assignment_id,))
            current = cur.fetchone()
            if not current:
                return False

            prev_status = current["assignment_status"]
            prev_notes = current["review_notes"] or ""
            ref = current["refund_reference"]
            emp_id = current["assigned_employee_id"]
            emp_name = current["assigned_employee_name"]

            now_str = datetime.now().isoformat()
            updated_notes = f"{prev_notes}\n[{now_str}] Status -> {new_status}: {review_notes or ''}".strip()

            conn.execute("""
                UPDATE investigation_assignments
                SET assignment_status = ?, last_updated_at = ?, review_notes = ?
                WHERE assignment_id = ?;
            """, (new_status, now_str, updated_notes, assignment_id))

            conn.execute("""
                INSERT INTO assignment_audit_log (
                    assignment_id, refund_reference,
                    previous_employee_id, previous_employee_name,
                    new_employee_id, new_employee_name,
                    previous_status, new_status,
                    changed_by, change_reason, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                assignment_id,
                ref,
                emp_id,
                emp_name,
                emp_id,
                emp_name,
                prev_status,
                new_status,
                changed_by,
                change_reason or f"Status changed from {prev_status} to {new_status}",
                now_str,
            ))
            return True
    finally:
        conn.close()


def reassign_employee(
    assignment_id: str,
    new_employee_id: str,
    changed_by: str,
    change_reason: str,
    review_notes: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """Reassigns an investigation to a different employee and records in the audit log."""
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cur = conn.cursor()
            # Fetch current assignment
            cur.execute("SELECT * FROM investigation_assignments WHERE assignment_id = ?;", (assignment_id,))
            current = cur.fetchone()
            if not current:
                return False

            # Fetch new employee details
            cur.execute("SELECT * FROM employees WHERE employee_id = ?;", (new_employee_id,))
            new_emp = cur.fetchone()
            if not new_emp:
                raise ValueError(f"Target employee '{new_employee_id}' not found in employee directory.")

            prev_emp_id = current["assigned_employee_id"]
            prev_emp_name = current["assigned_employee_name"]
            prev_status = current["assignment_status"]
            prev_notes = current["review_notes"] or ""
            ref = current["refund_reference"]

            new_emp_name = new_emp["employee_name"]
            new_dept = new_emp["department"]
            now_str = datetime.now().isoformat()
            new_status = "ASSIGNED" if prev_status == "UNASSIGNED" else prev_status

            updated_notes = f"{prev_notes}\n[{now_str}] Reassigned to {new_emp_name} ({new_dept}) by {changed_by}: {change_reason}".strip()

            conn.execute("""
                UPDATE investigation_assignments
                SET assigned_employee_id = ?, assigned_employee_name = ?,
                    assigned_department = ?, assignment_method = 'MANUAL_SUPERVISOR',
                    assignment_status = ?, last_updated_at = ?, review_notes = ?
                WHERE assignment_id = ?;
            """, (new_employee_id, new_emp_name, new_dept, new_status, now_str, updated_notes, assignment_id))

            conn.execute("""
                INSERT INTO assignment_audit_log (
                    assignment_id, refund_reference,
                    previous_employee_id, previous_employee_name,
                    new_employee_id, new_employee_name,
                    previous_status, new_status,
                    changed_by, change_reason, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                assignment_id,
                ref,
                prev_emp_id,
                prev_emp_name,
                new_employee_id,
                new_emp_name,
                prev_status,
                new_status,
                changed_by,
                change_reason,
                now_str,
            ))
            return True
    finally:
        conn.close()


# ==============================================================================
# 4. QUERY HELPERS FOR UI & REPORTING
# ==============================================================================

def list_all_employees(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Returns all employees with their current active workload calculated."""
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM employees ORDER BY department, role, employee_name;")
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["active_workload"] = get_employee_active_workload(r["employee_id"], db_path)
        return rows
    finally:
        conn.close()


def get_assignment_by_thread(thread_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves an assignment record by investigation thread ID."""
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM investigation_assignments WHERE investigation_thread_id = ?;", (thread_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_assignment_by_id(assignment_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves an assignment record by assignment ID."""
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM investigation_assignments WHERE assignment_id = ?;", (assignment_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_human_action_queue(
    status_filter: Optional[str] = None,
    department_filter: Optional[str] = None,
    employee_id_filter: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """
    Returns the Human Action Queue containing all cases requiring human intervention.
    """
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        query = "SELECT * FROM investigation_assignments WHERE 1=1"
        params = []

        if status_filter and status_filter != "ALL":
            query += " AND assignment_status = ?"
            params.append(status_filter)

        if department_filter and department_filter != "ALL":
            query += " AND assigned_department = ?"
            params.append(department_filter)

        if employee_id_filter and employee_id_filter != "ALL":
            query += " AND assigned_employee_id = ?"
            params.append(employee_id_filter)

        query += " ORDER BY last_updated_at DESC;"

        cur = conn.cursor()
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_assignment_audit_history(assignment_id: str, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Returns the immutable audit log for an assignment."""
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM assignment_audit_log
            WHERE assignment_id = ?
            ORDER BY timestamp ASC;
        """, (assignment_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def toggle_employee_availability(
    employee_id: str,
    new_status: str,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """Toggles employee availability status ('AVAILABLE', 'ON_LEAVE', 'BUSY')."""
    if new_status not in {"AVAILABLE", "ON_LEAVE", "BUSY"}:
        raise ValueError(f"Invalid availability status: {new_status}")
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            now_str = datetime.now().isoformat()
            cur = conn.cursor()
            cur.execute("""
                UPDATE employees
                SET availability_status = ?, updated_at = ?
                WHERE employee_id = ?;
            """, (new_status, now_str, employee_id))
            return cur.rowcount > 0
    finally:
        conn.close()


def toggle_employee_active_status(
    employee_id: str,
    active_status: int,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """Toggles employee active status (1 = Active, 0 = Inactive)."""
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            now_str = datetime.now().isoformat()
            cur = conn.cursor()
            cur.execute("""
                UPDATE employees
                SET active_status = ?, updated_at = ?
                WHERE employee_id = ?;
            """, (1 if active_status else 0, now_str, employee_id))
            return cur.rowcount > 0
    finally:
        conn.close()


def add_new_employee(
    employee_id: str,
    employee_name: str,
    department: str,
    role: str,
    maximum_active_cases: int = 5,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """Adds a new employee to the directory."""
    init_assignment_tables(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            now_str = datetime.now().isoformat()
            conn.execute("""
                INSERT INTO employees (
                    employee_id, employee_name, department, role,
                    active_status, availability_status, maximum_active_cases,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 'AVAILABLE', ?, ?, ?);
            """, (employee_id, employee_name, department, role, maximum_active_cases, now_str, now_str))
            return True
    finally:
        conn.close()

