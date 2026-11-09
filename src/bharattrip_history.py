"""
bharattrip_history.py
---------------------
Persistent SQLite storage and conversation management for BharatTrip Refund Operations Assistant.
Stores user complaints, investigation findings, reports, and supervisor audit notes
independently of Streamlit session state so conversations persist across browser refreshes
and application restarts while preserving exact LangGraph thread continuity.
"""

from __future__ import annotations

import sqlite3
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


def get_default_db_path() -> str:
    """Resolves operations SQLite database in package data/ directory or CWD."""
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_data = os.path.join(pkg_root, "data")
    if os.path.exists(target_data):
        return os.path.join(target_data, "bharattrip_operations.db")
    return "bharattrip_operations.db"


DEFAULT_DB_PATH = get_default_db_path()


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(database=db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_history_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initializes the chat history tables in SQLite without affecting checkpoint tables."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS refund_chat_history (
                    conversation_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    refund_reference TEXT,
                    category TEXT,
                    investigation_status TEXT,
                    human_review_status TEXT, -- 'NOT_REQUIRED', 'AWAITING_REVIEW', 'COMPLETED', 'NEEDS_CLARIFICATION'
                    human_decision TEXT,
                    complaint_text TEXT,
                    final_report TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS refund_chat_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, -- 'user', 'assistant', 'supervisor'
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_history_updated
                ON refund_chat_history(updated_at DESC);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
                ON refund_chat_messages(conversation_id);
            """)
    finally:
        conn.close()


def generate_conversation_title(
    complaint_text: str,
    refund_ref: Optional[str] = None,
    category: Optional[str] = None
) -> str:
    """Generates an intuitive, readable title for a conversation."""
    if refund_ref and refund_ref not in {"None", "MISSING", "AMBIGUOUS"}:
        cat_label = (category or "").replace("_", " ").title()
        if cat_label and cat_label != "Unknown":
            return f"{refund_ref}: {cat_label}"
        return f"{refund_ref} Investigation"

    clean_text = complaint_text.strip().replace("\n", " ")
    if len(clean_text) > 40:
        clean_text = clean_text[:37] + "..."
    return clean_text or "New Investigation"


def save_or_update_conversation(
    conversation_id: str,
    thread_id: str,
    title: str,
    refund_reference: Optional[str] = None,
    category: Optional[str] = None,
    investigation_status: Optional[str] = "IN_PROGRESS",
    human_review_status: Optional[str] = "NOT_REQUIRED",
    human_decision: Optional[str] = None,
    complaint_text: Optional[str] = None,
    final_report: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Inserts or updates a conversation record with timestamps."""
    init_history_db(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT created_at FROM refund_chat_history WHERE conversation_id = ?",
                (conversation_id,)
            )
            existing = cursor.fetchone()
            if existing:
                created_at = existing["created_at"]
                cursor.execute("""
                    UPDATE refund_chat_history
                    SET thread_id = ?,
                        title = ?,
                        refund_reference = ?,
                        category = ?,
                        investigation_status = ?,
                        human_review_status = ?,
                        human_decision = ?,
                        complaint_text = ?,
                        final_report = ?,
                        updated_at = ?
                    WHERE conversation_id = ?
                """, (
                    thread_id,
                    title,
                    refund_reference,
                    category,
                    investigation_status,
                    human_review_status,
                    human_decision,
                    complaint_text,
                    final_report,
                    now_str,
                    conversation_id
                ))
            else:
                cursor.execute("""
                    INSERT INTO refund_chat_history (
                        conversation_id, thread_id, title, refund_reference,
                        category, investigation_status, human_review_status,
                        human_decision, complaint_text, final_report,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    conversation_id,
                    thread_id,
                    title,
                    refund_reference,
                    category,
                    investigation_status,
                    human_review_status,
                    human_decision,
                    complaint_text,
                    final_report,
                    now_str,
                    now_str
                ))
    finally:
        conn.close()


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    db_path: str = DEFAULT_DB_PATH
) -> int:
    """Appends a single message (user, assistant, supervisor) to the conversation history."""
    init_history_db(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO refund_chat_messages (conversation_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
            """, (conversation_id, role, content, now_str))
            # Also bump conversation updated_at
            cursor.execute(
                "UPDATE refund_chat_history SET updated_at = ? WHERE conversation_id = ?",
                (now_str, conversation_id)
            )
            return cursor.lastrowid or 0
    finally:
        conn.close()


def get_conversation(
    conversation_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> Optional[Dict[str, Any]]:
    """Retrieves full conversation metadata."""
    init_history_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM refund_chat_history WHERE conversation_id = ?",
            (conversation_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_conversation_messages(
    conversation_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Retrieves all conversation messages in chronological order."""
    init_history_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM refund_chat_messages WHERE conversation_id = ? ORDER BY message_id ASC",
            (conversation_id,)
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_conversations(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Lists all stored conversations sorted by most recently updated."""
    init_history_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM refund_chat_history ORDER BY updated_at DESC"
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def rename_conversation(
    conversation_id: str,
    new_title: str,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """Updates the conversation title."""
    init_history_db(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE refund_chat_history
                SET title = ?, updated_at = ?
                WHERE conversation_id = ?
            """, (new_title.strip(), now_str, conversation_id))
            return cursor.rowcount > 0
    finally:
        conn.close()


def delete_conversation(
    conversation_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """
    Safely deletes a conversation and its messages from chat history.
    Preserves LangGraph checkpoints for audit compliance.
    """
    init_history_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM refund_chat_messages WHERE conversation_id = ?",
                (conversation_id,)
            )
            cursor.execute(
                "DELETE FROM refund_chat_history WHERE conversation_id = ?",
                (conversation_id,)
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def clear_all_conversations(db_path: str = DEFAULT_DB_PATH) -> int:
    """Clears all chat history records while preserving LangGraph checkpoints."""
    init_history_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM refund_chat_messages")
            cursor.execute("DELETE FROM refund_chat_history")
            return cursor.rowcount
    finally:
        conn.close()

