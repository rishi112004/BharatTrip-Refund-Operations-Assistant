"""
test_bharattrip_assignment.py
------------------------------
Unit and integration tests for automatic workload-based human assignment
in BharatTrip Refund Operations Assistant.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
import sqlite3

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import bharattrip_assignment as asgn

TEST_ASGN_DB = "test_asgn_temp.db"


class TestBharatTripAssignment(unittest.TestCase):
    """Test suite for deterministic routing, workload selection, and audit logging."""

    @classmethod
    def setUpClass(cls):
        if os.path.exists(TEST_ASGN_DB):
            try:
                os.remove(TEST_ASGN_DB)
            except Exception:
                pass
        asgn.init_assignment_tables(TEST_ASGN_DB)

    @classmethod
    def tearDownClass(cls):
        import gc
        gc.collect()
        if os.path.exists(TEST_ASGN_DB):
            try:
                os.remove(TEST_ASGN_DB)
            except Exception:
                pass

    def setUp(self):
        # Re-initialize clean assignment state for every test
        conn = asgn.get_db_connection(TEST_ASGN_DB)
        with conn:
            conn.execute("DELETE FROM investigation_assignments;")
            conn.execute("DELETE FROM assignment_audit_log;")
            conn.execute("DELETE FROM employees;")
        conn.close()
        asgn.init_assignment_tables(TEST_ASGN_DB)

    # 1. Deterministic Department & Role Routing
    def test_department_routing_rules(self):
        cases = [
            ("MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], "Finance", "Analyst"),
            ("CLOSED_BUT_PENDING", ["CLOSED_BUT_PENDING"], "Finance", "Analyst"),
            ("DUPLICATE_IN_FINANCE", ["DUPLICATE_IN_FINANCE"], "Finance", "Supervisor"),
            ("SUPPORT_DUPLICATE_REVIEW", ["SUPPORT_DUPLICATE_REVIEW"], "Support", "Supervisor"),
            ("CONFLICTING_SUPPORT_DUPLICATE", ["CONFLICTING_SUPPORT_DUPLICATES"], "Support", "Supervisor"),
            ("UNVERIFIED_POLICY_AUTHORIZATION", ["UNVERIFIED_POLICY_AUTHORIZATION"], "Finance", "Policy Owner"),
            ("CLOSED_BUT_DECLINED", ["CLOSED_BUT_DECLINED"], "Operations", "Operations Manager"),
        ]
        for cat, flags, expected_dept, expected_role in cases:
            dept, role, _ = asgn.route_issue_to_department_and_role(cat, flags)
            self.assertEqual(dept, expected_dept, f"Mismatch for {cat}: expected {expected_dept}, got {dept}")
            self.assertEqual(role, expected_role, f"Mismatch for {cat}: expected {expected_role}, got {role}")

    # 2. Lowest Workload Selection
    def test_lowest_workload_selection(self):
        # Add 2 Finance Analysts: EMP-A with 1 case, EMP-B with 0 cases
        asgn.add_new_employee("EMP-FA-1", "Analyst One (Sample)", "Finance", "Analyst", maximum_active_cases=5, db_path=TEST_ASGN_DB)
        asgn.add_new_employee("EMP-FA-2", "Analyst Two (Sample)", "Finance", "Analyst", maximum_active_cases=5, db_path=TEST_ASGN_DB)

        # Assign one case to EMP-FA-1
        asgn.assign_investigation_case("RF-1001", "thread-1", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)

        # The next case must be assigned to EMP-FA-2 because they have 0 active cases
        res = asgn.assign_investigation_case("RF-1002", "thread-2", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)
        self.assertEqual(res["assigned_employee_id"], "EMP-FA-2")
        self.assertEqual(res["workload_at_assignment"], 0)

    # 3. Maximum Workload Capacity Enforcement
    def test_maximum_workload_capacity_enforcement(self):
        # Create an analyst with max 2 cases
        asgn.add_new_employee("EMP-CAP-1", "Capacity Tester (Sample)", "Finance", "Analyst", maximum_active_cases=2, db_path=TEST_ASGN_DB)
        # Deactivate default sample analysts so EMP-CAP-1 is only candidate
        conn = asgn.get_db_connection(TEST_ASGN_DB)
        with conn:
            conn.execute("UPDATE employees SET active_status = 0 WHERE employee_id != 'EMP-CAP-1';")
        conn.close()

        # Fill up 2 cases
        asgn.assign_investigation_case("RF-CAP-1", "thread-c1", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)
        asgn.assign_investigation_case("RF-CAP-2", "thread-c2", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)

        # Third case must fail to assign and become UNASSIGNED
        res3 = asgn.assign_investigation_case("RF-CAP-3", "thread-c3", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)
        self.assertEqual(res3["assignment_status"], "UNASSIGNED")
        self.assertEqual(res3["assignment_method"], "UNASSIGNED_NO_ELIGIBLE")
        self.assertIsNone(res3["assigned_employee_id"])

    # 4. Inactive Employee Exclusion
    def test_inactive_employee_exclusion(self):
        asgn.add_new_employee("EMP-INACT", "Inactive User (Sample)", "Support", "Supervisor", db_path=TEST_ASGN_DB)
        asgn.toggle_employee_active_status("EMP-INACT", active_status=0, db_path=TEST_ASGN_DB)

        conn = asgn.get_db_connection(TEST_ASGN_DB)
        with conn:
            conn.execute("UPDATE employees SET active_status = 0 WHERE employee_id != 'EMP-INACT';")
        conn.close()

        res = asgn.assign_investigation_case("RF-SUP-1", "thread-sup1", "SUPPORT_DUPLICATE_REVIEW", ["SUPPORT_DUPLICATE_REVIEW"], db_path=TEST_ASGN_DB)
        self.assertEqual(res["assignment_status"], "UNASSIGNED")

    # 5. Unavailable Employee Exclusion
    def test_unavailable_employee_exclusion(self):
        # Mark all sample employees ON_LEAVE
        conn = asgn.get_db_connection(TEST_ASGN_DB)
        with conn:
            conn.execute("UPDATE employees SET availability_status = 'ON_LEAVE';")
        conn.close()

        res = asgn.assign_investigation_case("RF-1148", "thread-dup", "DUPLICATE_IN_FINANCE", ["DUPLICATE_IN_FINANCE"], db_path=TEST_ASGN_DB)
        self.assertEqual(res["assignment_status"], "UNASSIGNED")

    # 6. Workload Count Decrement after Case Resolution
    def test_workload_decrement_after_resolution(self):
        res = asgn.assign_investigation_case("RF-RES-1", "thread-r1", "CLOSED_BUT_PENDING", ["CLOSED_BUT_PENDING"], db_path=TEST_ASGN_DB)
        emp_id = res["assigned_employee_id"]

        workload_before = asgn.get_employee_active_workload(emp_id, db_path=TEST_ASGN_DB)
        self.assertGreaterEqual(workload_before, 1)

        # Resolve the case
        asgn.update_assignment_status(res["assignment_id"], "RESOLVED", changed_by="Supervisor", db_path=TEST_ASGN_DB)

        workload_after = asgn.get_employee_active_workload(emp_id, db_path=TEST_ASGN_DB)
        self.assertEqual(workload_after, workload_before - 1)

    # 7. Duplicate Assignment Prevention (Idempotent for same thread)
    def test_duplicate_assignment_prevention(self):
        res1 = asgn.assign_investigation_case("RF-IDEMP", "thread-same", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)
        res2 = asgn.assign_investigation_case("RF-IDEMP", "thread-same", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)
        self.assertEqual(res1["assignment_id"], res2["assignment_id"])

    # 8. Reassignment & Audit Trail
    def test_reassignment_audit_trail(self):
        res = asgn.assign_investigation_case("RF-AUDIT", "thread-aud", "MISSING_IN_FINANCE", ["MISSING_IN_FINANCE"], db_path=TEST_ASGN_DB)
        asgn_id = res["assignment_id"]
        orig_emp_id = res["assigned_employee_id"]

        # Reassign to a different supervisor
        ok = asgn.reassign_employee(
            assignment_id=asgn_id,
            new_employee_id="EMP-FIN-02",
            changed_by="Manager",
            change_reason="Priority escalation",
            db_path=TEST_ASGN_DB
        )
        self.assertTrue(ok)

        # Verify audit history
        audit = asgn.get_assignment_audit_history(asgn_id, db_path=TEST_ASGN_DB)
        self.assertGreaterEqual(len(audit), 2)
        latest_audit = audit[-1]
        self.assertEqual(latest_audit["previous_employee_id"], orig_emp_id)
        self.assertEqual(latest_audit["new_employee_id"], "EMP-FIN-02")
        self.assertEqual(latest_audit["changed_by"], "Manager")
        self.assertIn("Priority escalation", latest_audit["change_reason"])


if __name__ == "__main__":
    unittest.main()

