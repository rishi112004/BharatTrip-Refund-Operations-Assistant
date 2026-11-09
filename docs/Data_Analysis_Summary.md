# BharatTrip Refund Operations — Workbook & Data Quality Analysis

## 1. Executive Summary

An exhaustive analysis was performed on the official BharatTrip Excel workbook (`BharatTrip_Refund_Data.xlsx`). The dataset models a high-volume B2B travel operations environment where corporate and retail travel agents request refunds for cancelled or modified bookings across Support, Finance, and Escalation departments.

---

## 2. Dataset Structure & Dimensions

The workbook contains 9 sheets, with 3 primary operational trackers:

| Sheet Name | Raw Rows | Header / Index | Usable Records | Purpose |
|---|---|---|---|---|
| **`Support_Tracker`** | 756 | Row 1 (Header) | **755** rows | Records initial refund requests logged by Support agents. |
| **`Finance_Tracker`** | 690 | Row 1 (Header) | **689** rows | Records financial payout processing, fee deductions, and payment release. |
| **`Escalation`** | 156 | Row 1 (Header) | **155** rows | Logs tickets escalated due to agent disputes or delays. |
| `Key Metrics` | - | - | Dashboard | Summary metrics. |
| `Read_Me` / `Analysis` | - | - | Reference | Notes and exploratory calculations. |

---

## 3. Reference Normalization & Entity Alignment

### Reference Variations Identified:
- **Case Variations**: `rf-1650` vs `RF-1650`.
- **Whitespace / Formatting**: `RF 1234`, `RF-1234`, `RF- 1175`.
- **Revised Suffixes**: `RF-1175 (revised)`.
- **Appended Agent Metadata**: `RF-1175, Orbit Travels`.
- **Ambiguous Queries**: Cells containing multiple candidate references (e.g. `RF-1001 or RF-1002`).

### Entity Coverage:
- Unique Normalized Entities in Support: **733**
- Unique Normalized Entities in Finance: **680**
- Total Distinct Entities across both: **749**

---

## 4. Duplicate Record Analysis

### Support Duplicates (44 Rows / 22 Unique Ticket IDs)
1. **Conflicting Duplicates (10 pairs)**:
   - Different amounts or statuses for the exact same ticket ID.
   - Examples: `RF-1041`, `RF-1181`, `RF-1232`, `RF-1368`, `RF-1435`, `RF-1791`, `RF-1847`.
   - **Governance Policy**: *Never automatically pick one record over another.* Automatically flagged for human supervisor review under `SUPPORT_DUPLICATE_REVIEW`.
2. **Exact Duplicate Rows (12 pairs)**:
   - Exact duplicate row entries representing re-entry of the same ticket.

### Finance Duplicates (18 Rows / 9 Unique Ref Nos)
- Exactly 9 references appear twice in `Finance_Tracker`:
  `RF-1148`, `RF-1177`, `RF-1238`, `RF-1393`, `RF-1395`, `RF-1545`, `RF-1606`, `RF-1672`, `RF-1851`.
- **Governance Policy**: *Never add duplicate Finance amounts together.* Flagged immediately as `DUPLICATE_IN_FINANCE` to halt processing and prevent double-disbursement.

---

## 5. Cross-Department Discrepancy Breakdown

Categorization across all 749 distinct entities in the workbook:

| Primary Reconciliation Category | Entity Count | Operational Meaning |
|---|---|---|
| **`CLEAN_MATCH_REFUNDED`** | **473** | Support Closed, Finance Refund Done, amounts match or variance ≤ 0.01. |
| **`EXPLAINED_DEDUCTION`** | **94** | `Support Claim - Finance Paid = Recorded Deduction`. (Policy authorization unverified). |
| **`MISSING_IN_FINANCE`** | **69** | Logged in Support, but zero record found in Finance Tracker. |
| **`FINANCE_ONLY_RECORDS`** | **16** | Payout processed by Finance, but no ticket logged in Support Tracker. |
| **`CLOSED_BUT_PENDING`** | **19** | Support marked ticket 'Closed', but Finance status is still 'Pending Payout'. |
| **`CLOSED_BUT_DECLINED`** | **17** | Support marked ticket 'Closed', but Finance explicitly declined payout. |
| **`DUPLICATE_IN_FINANCE`** | **9** | Reference appears multiple times in Finance. Risk of double payout. |
| **`SUPPORT_DUPLICATE_REVIEW`** | **9** | Conflicting Support rows requiring supervisor resolution. |
| **`PAID_BUT_SUPPORT_WORKING`** | **19** | Finance completed payout, but Support still shows ticket as 'Working'. |
| **`IN_PROGRESS_OR_DECLINED`** | **24** | Consistent in-progress or declined status across both departments. |
| **Total Unique Entities** | **749** | Comprehensive 100% workbook coverage. |

---

## 6. Root Cause Analysis

1. **Departmental Silos**: Support agents manually close tickets assuming queuing in Finance equals settlement, leading to false completion notifications to agents (`CLOSED_BUT_PENDING`).
2. **Handover Drop-Offs**: 69 tickets were logged by Support but never received or entered by Finance (`MISSING_IN_FINANCE`).
3. **Unverified Fee Deductions**: Finance records deductions without documented policy rule IDs or cancellation penalty slabs.
4. **Disbursal Duplication**: 9 Finance references appear twice, creating high financial leakage risk.

