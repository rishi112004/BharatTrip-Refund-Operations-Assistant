"""
bharattrip_groq_config.py
--------------------------
Centralized Groq configuration, credential management, model resolution,
and connectivity verification for BharatTrip Refund Operations Assistant.

Guarantees:
- Never hardcodes or logs API credentials.
- Masks API keys in diagnostic output.
- Loads configuration via python-dotenv from package root or environment.
- Configures default model: openai/gpt-oss-120b.
- Verifies real API connectivity with graceful error handling.
- Does not introduce other LLM providers.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional, Tuple
from dotenv import load_dotenv

# Ensure package root .env is loaded
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOTENV_PATH = os.path.join(PACKAGE_ROOT, ".env")
if os.path.exists(DOTENV_PATH):
    load_dotenv(DOTENV_PATH, override=True)
else:
    load_dotenv()

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def get_groq_api_key() -> Optional[str]:
    """Retrieves the Groq API key from environment, stripped of whitespace."""
    key = os.getenv("GROQ_API_KEY")
    if key:
        clean = key.strip()
        return clean if clean else None
    return None


def get_configured_model() -> str:
    """Retrieves configured Groq model or returns default 'openai/gpt-oss-120b'."""
    model = os.getenv("GROQ_MODEL")
    if model and model.strip():
        return model.strip()
    return DEFAULT_GROQ_MODEL


def get_masked_api_key() -> str:
    """Returns a safe, masked representation of the API key for UI status display."""
    key = get_groq_api_key()
    if not key:
        return "Not Configured"
    if len(key) <= 8:
        return "****"
    return f"{key[:6]}...{key[-4:]}"


def get_groq_chat_model(
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
) -> Any:
    """
    Initializes and returns a ChatGroq instance using environment configuration.
    Raises ValueError if API key is missing.
    """
    from langchain_groq import ChatGroq

    effective_key = api_key or get_groq_api_key()
    if not effective_key:
        raise ValueError(
            "GROQ_API_KEY is not configured. Please add GROQ_API_KEY to your .env file."
        )

    effective_model = model_name or get_configured_model()
    return ChatGroq(
        model=effective_model,
        api_key=effective_key,
        temperature=temperature,
    )


def verify_groq_connectivity(
    test_prompt: str = "Ping",
    timeout_seconds: int = 15
) -> Dict[str, Any]:
    """
    Performs an actual minimal API invocation against Groq to verify credentials and model.
    Never exposes the API key in the result.
    """
    api_key = get_groq_api_key()
    model = get_configured_model()

    if not api_key:
        return {
            "status": "MISSING_KEY",
            "connected": False,
            "model": model,
            "masked_key": "None",
            "message": "Groq API key is missing. Please configure GROQ_API_KEY in BharatTrip_Submission/.env.",
        }

    try:
        llm = get_groq_chat_model(model_name=model, temperature=0.0, api_key=api_key)
        response = llm.invoke(test_prompt)
        content = getattr(response, "content", str(response)).strip()
        return {
            "status": "OK",
            "connected": True,
            "model": model,
            "masked_key": get_masked_api_key(),
            "sample_response": content[:100],
            "message": f"Successfully connected to Groq model '{model}'.",
        }
    except Exception as exc:
        err_type = type(exc).__name__
        err_msg = str(exc)
        # Scrub any accidental key exposure from exception message
        if api_key in err_msg:
            err_msg = err_msg.replace(api_key, "[MASKED_KEY]")

        return {
            "status": "API_ERROR",
            "connected": False,
            "model": model,
            "masked_key": get_masked_api_key(),
            "error_type": err_type,
            "message": f"Groq API connection error ({err_type}): {err_msg}",
        }

