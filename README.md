# BharatTrip AI Refund Operations Assistant

An enterprise-grade, deterministic AI operations platform designed for BharatTrip to reconcile cross-departmental flight refund disputes across **Customer Support**, **Finance Operations**, and **Escalation Management**.

Orchestrated using **LangGraph StateGraph**, powered by **Groq (`openai/gpt-oss-120b`)**, and served via an interactive **Streamlit** operations console backed by automatic workload-based human assignment, persistent **SQLite** state checkpointing, and grounded conversational intelligence.

---

## 🚀 Key Highlights & Architecture Principles

1. **Deterministic Financial Truth**: Financial arithmetic, payout statuses, row tracing, and duplicate detection are handled **100% deterministically via Pandas and OpenPyXL**. The LLM is **never** permitted to calculate balances or guess transaction states.
2. **Strictly Advisory — Zero Autonomous Payouts**: The system produces audit-ready operational advisories for human agents. It will **never** authorize, trigger, or execute a bank disbursement or ledger modification. All execution requests are intercepted and refused with governance policies.
3. **No Vector Databases for Financial Auditing**: Vector databases (Chroma, FAISS, Pinecone) rely on approximate semantic proximity. To prevent catastrophic financial hallucinations, vector search is banned from payment verification.
4. **Automatic Workload-Based Assignment**: When human review is required, cases are deterministically routed to the responsible department (Finance, Support, Operations) and auto-assigned to the eligible employee with the lowest active workload using round-robin tie breaking and strict capacity enforcement.
5. **Grounded AI Refund Chatbot**: Employees can ask natural-language questions (e.g., *"Why is RF-1570 awaiting review?"*, *"What happened to RF-1252?"*, *"Which cases have duplicate Finance records?"*). Responses are synthesized by Groq using **only verified reconciliation records** as ground truth.
6. **Human-in-the-Loop Interrupts**: High-risk financial states (declined transactions, duplicate records, unverified deductions, missing finance records) atomically pause the LangGraph workflow via SQLite checkpoints until an authorized supervisor provides sign-off.
7. **Multi-Issue Secondary Classification**: Preserves rich operational context—such as flagging a record simultaneously as `SUPPORT_DUPLICATE_REVIEW` and `PAID_BUT_SUPPORT_WORKING` (e.g., ticket `RF-1232`).
8. **Robust Reference Normalization**: Seamlessly resolves real-world noise, punctuation variations, trailing agent notes (`RF-1175, Orbit Travels`), and suffixes (`RF-1252 (revised)`).

---

## 📁 Repository Structure

```text
BharatTrip_Submission/
├── README.md                              # Comprehensive documentation and evaluator guide
├── requirements.txt                      # Clean runtime dependencies (no Chroma, FAISS, or OpenAI)
├── .env.example                          # Environment variable configuration template (openai/gpt-oss-120b)
├── .gitignore                            # Excludes .env, virtual environments, SQLite databases, caches
│
├── src/                                  # Core application source code
│   ├── bharattrip_reconciliation.py      # Deterministic cross-sheet reconciliation engine
│   ├── bharattrip_groq_config.py         # Centralized Groq credentials, model resolution & connectivity
│   ├── bharattrip_assignment.py          # Automatic workload assignment & employee directory
│   ├── bharattrip_chatbot.py             # Grounded AI operations chatbot backed by Groq
│   ├── bharattrip_refund_backend.py      # LangGraph StateGraph workflow & interrupt nodes
│   ├── bharattrip_history.py             # SQLite operational conversation & audit persistence
│   └── streamlit_bharattrip_refund.py    # Enterprise Streamlit UI with 5 main operational views
│
├── data/                                 # Datasets & generated audit reports
│   ├── BharatTrip_Refund_Data.xlsx       # Master Excel source of truth (read-only, unmodified)
│   ├── BharatTrip_Reconciliation_Report.csv   # Deterministic reconciliation export (CSV)
│   └── BharatTrip_Reconciliation_Report.xlsx  # Deterministic reconciliation export (XLSX)
│
├── tests/                                # Comprehensive test suite (84/84 passing tests)
│   ├── test_bharattrip_reconciliation.py # 21 tests: normalization, duplicates, arithmetic
│   ├── test_bharattrip_refund_workflow.py# 13 tests: LangGraph nodes, interrupt/resume, benchmarks
│   ├── test_streamlit_bharattrip_refund.py# 16 tests: UI states, presets, safety guardrails
│   ├── test_bharattrip_chat_history.py   # 10 tests: SQLite threads, auto-titling, audit notes
│   ├── test_bharattrip_assignment.py     # 8 tests: workload routing, capacity, availability, round-robin
│   ├── test_bharattrip_chatbot.py        # 9 tests: grounded chatbot, refusal of execution, masked keys
│   └── test_live_groq_connectivity.py    # 1 test: Real live API network connectivity with Groq
│
├── docs/                                 # Deliverables and formal reports
│   ├── Final_Project_Report.pdf          # 3-page executive & technical PDF report
│   ├── Architecture.png                  # High-resolution architectural diagram
│   └── Data_Analysis_Summary.md          # Dataset metrics, duplicate analysis & root causes
│
├── prompt_history/                       # Authentic development trace
│   └── actual_development_prompts.md     # Chronological log of actual prompts used
│
└── screenshots/                          # High-fidelity application captures
    ├── application_homepage.png          # Streamlit investigation console
    ├── refund_investigation_results.png  # Detailed report cards & financial variance analysis
    ├── human_review_example.png          # LangGraph supervisor review interrupt interface
    └── chat_history_example.png          # Persistent conversation management & audit logs
```

---

## 🛠️ Technology Stack

| Component | Technology | Role |
| :--- | :--- | :--- |
| **Orchestration** | `langgraph` (v1.0+) | Deterministic StateGraph, state machine & human-in-the-loop interrupts |
| **LLM & Inference** | `ChatGroq` (`openai/gpt-oss-120b`) | Natural-language complaint entity extraction & grounded AI operations Q&A |
| **Reconciliation Engine** | `pandas`, `openpyxl` | Exact reference indexing, duplicate detection, arithmetic variance audit |
| **Workload Assignment** | `sqlite3` (Atomic Transactions) | Capacity tracking, round-robin tie breaking, immutable assignment audit logs |
| **State Checkpointing** | `langgraph-checkpoint-sqlite` | Thread-level state persistence across pauses and resumes |
| **Operational History** | `sqlite3` (`bharattrip_operations.db`) | Conversation tracking, search, title generation, and audit note storage |
| **User Interface** | `streamlit` | 5-section enterprise operations dashboard with role-tailored views |
| **Configuration** | `python-dotenv` | Secure API key and runtime parameter management |

---

## ⚙️ Installation & Quickstart

### 1. Prerequisites
- Python 3.10, 3.11, or 3.12+ (tested up to Python 3.14 on Windows 11)
- A valid [Groq API Key](https://console.groq.com)

### 2. Setup Virtual Environment
```bash
# Clone or navigate to the submission folder
cd BharatTrip_Submission

# Create and activate virtual environment
# Windows PowerShell:
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS / Linux:
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
Copy `.env.example` to `.env` and set your Groq credentials:
```bash
# Windows:
copy .env.example .env

# macOS / Linux:
cp .env.example .env
```
Edit `.env`:
```env
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b
```

---

## 🖥️ Running the Application

Launch the Streamlit web application from within `BharatTrip_Submission/`:

```bash
python -m streamlit run src/streamlit_bharattrip_refund.py
```

The application opens at `http://localhost:8501`.

### Five Primary Operational Sections
1. **🔍 Refund Investigation**: Natural-language complaint intake with 10 benchmark presets, instant deterministic cards, and auto-assigned supervisor review interrupts.
2. **📋 Human Action Queue**: Centralized queue of all cases requiring manual review with department/status filters, inline status updates, and supervisor reassignment.
3. **👥 Employee Work Queues**: Employee directory with active workload/capacity tracking, staff availability toggles, and individualized work queues.
4. **💬 AI Refund Chatbot**: Grounded conversational assistant answering plain-English questions about tickets, duplicate records, and priority investigations using actual workbook data.
5. **📊 Reports & History**: Dataset statistics, CSV/Excel reconciliation report downloads, persistent conversation replay, and administrator technical audit drawer.

---

## 🧪 Running the Verification Test Suite

The test suite includes **84 automated tests** across 7 test modules:

```bash
# Run all tests using unittest discovery:
python -m unittest discover -s tests -p "test_*.py" -v
```

### Test Suite Coverage Breakdown

```text
======================================================================
1. tests/test_bharattrip_reconciliation.py (21 tests)
   - Reference normalization (spaces, cases, hyphens, prefixes, suffixes)
   - Ambiguous and compound reference flagging
   - Support duplicate detection (22 duplicate IDs / 44 rows)
   - Finance duplicate detection (9 duplicate refs / 18 rows)
   - 3-way deduction taxonomy (EXPLICIT_ZERO, RECORDED_POSITIVE, MISSING_UNKNOWN)
   - Arithmetic classification (EXPLAINED_DEDUCTION vs AMOUNT_MISMATCH)
   - Multi-issue flag preservation (e.g., RF-1232)
   - Unmodified source workbook verification

2. tests/test_bharattrip_refund_workflow.py (13 tests)
   - LangGraph StateGraph initialization & node routing
   - SQLite checkpointer state persistence
   - Human review interrupt pause and resumption
   - Benchmark verification: RF-1286, RF-1252, RF-1570, RF-1757, RF-1148, RF-1325, RF-999999
   - Autonomous refund execution blocking

3. tests/test_streamlit_bharattrip_refund.py (16 tests)
   - Session state initialization & UI helpers
   - Benchmark preset integrity and completeness
   - Governance disclaimer rendering & advisory wording
   - Report formatting & source Excel row number inclusion
   - Human review decision validation & state resumption
   - Self-containment check (zero external dependencies)

4. tests/test_bharattrip_chat_history.py (10 tests)
   - SQLite conversation schema creation (bharattrip_operations.db)
   - Conversation creation, listing, retrieval, renaming, and safe deletion
   - Descriptive auto-titling logic
   - Audit note logging & supervisor decision capture

5. tests/test_bharattrip_assignment.py (8 tests)
   - Deterministic department & role routing rules
   - Lowest workload employee selection
   - Maximum active workload capacity enforcement
   - Inactive & unavailable employee exclusion
   - Active workload count decrement upon case resolution
   - Idempotent duplicate assignment prevention
   - Supervisor reassignment with immutable audit logging

6. tests/test_bharattrip_chatbot.py (9 tests)
   - Groq configuration resolution (openai/gpt-oss-120b)
   - Masked API key representation (never exposes secret)
   - Missing API key friendly handling
   - Refusal of financial execution / payout requests
   - Grounded ticket inquiries (RF-1570 missing, RF-1252 deduction)
   - Grounded duplicate inquiries (RF-1148 list)
   - Unknown reference handling (never fabricates records)
   - Chat message persistence in SQLite history

7. tests/test_live_groq_connectivity.py (1 test)
   - Real live network API connectivity test verifying Groq account & openai/gpt-oss-120b
======================================================================
Ran 84 tests in ~21s — OK (All 84 tests passing)
```

---

## 👥 Candidate Information
- **Assignment**: BharatTrip AI Refund Operations Assistant
- **Role**: AI Operations Associate
- **Submission Date**: November 2026
- **Test Suite Result**: 84 / 84 Passed (100%)
