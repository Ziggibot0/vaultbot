"""
Agent-authored tool: ask_user
"""

SCHEMA = {"name": "ask_user", "description": "Send an interactive questionnaire to the user via the Obsidian plugin GUI and block until the user responds. The user can answer each question, pick \"I don't know\" for any question, and add free-text comments for nuance. Use this when you need the user's input to crystallize an idea or make a decision.", "parameters": {"properties": {"context": {"description": "Background context explaining what you're trying to decide or what information you need", "type": "string"}, "questions": {"description": "List of question objects to present to the user", "items": {"properties": {"default": {"description": "Default value: 'best_practices' to pre-select the 'I don't know, use best practices' option, or a string for text default", "type": "string"}, "id": {"description": "Unique key for this question (used as the key in the returned dict)", "type": "string"}, "options": {"description": "List of option strings (for radio type only)", "items": {"type": "string"}, "type": "array"}, "question": {"description": "The question text to display", "type": "string"}, "type": {"description": "'radio' for single-choice with options, 'text' for free-form input", "enum": ["radio", "text"], "type": "string"}}, "required": ["id", "question", "type"], "type": "object"}, "type": "array"}, "title": {"description": "Short title for the question card (e.g. 'Research approach')", "type": "string"}}, "required": ["title", "questions"], "type": "object"}}


"""
Agent-authored tool: ask_user

Sends an interactive questionnaire to the user via the Obsidian plugin GUI
and blocks until the user responds. The user can answer each question, pick
"I don't know, use best practices" for any question, and add free-text
comments for nuance. The agentic loop blocks until the user submits.

This is the tool that lets VaultBot extract ideas from the user — the user
dreams of all the parts, VaultBot crystallizes them into a finished product.
"""

import json
import threading
import time
import urllib.request
import uuid

# Module-level registry: request_id -> (event, response_dict)
# The /user_response HTTP endpoint reads this to unblock the waiting tool.
_pending_requests: dict[str, tuple[threading.Event, dict]] = {}


def _cleanup_stale():
    """Remove requests older than 10 minutes (defensive cleanup)."""
    now = time.time()
    stale = []
    for rid, (ev, _) in list(_pending_requests.items()):
        if not ev.is_set() and hasattr(ev, '_created_at') and (now - ev._created_at) > 600:
            stale.append(rid)
    for rid in stale:
        try:
            ev, _ = _pending_requests.pop(rid)
            ev.set()  # unblock with error
        except KeyError:
            pass


def run(args: dict) -> dict:
    """Send a questionnaire to the user and wait for their response.

    Args:
        title: Short title for the question card (e.g. "Research approach")
        context: Background context explaining what you're trying to decide
        questions: List of question objects, each with:
            - id: unique key for this question
            - question: the question text
            - type: "radio" (single choice) or "text" (free-form)
            - options: list of option strings (for radio type)
            - default: "best_practices" to pre-select the "I don't know" option,
                       or a string for text default

    Returns:
        dict with keys matching question ids, each value being the user's
        answer string. For "best_practices" selections, the value is the
        literal string "best_practices" — the caller should handle that
        by researching best practices for that decision.

    Example:
        run({
            "title": "Graph edge strategy",
            "context": "I'm building a knowledge graph and need to decide...",
            "questions": [
                {"id": "edge_types", "question": "Which edge typing approach?",
                 "type": "radio", "options": ["Typed", "Untyped", "Hybrid"],
                 "default": "best_practices"},
                {"id": "nuance", "question": "Any constraints?",
                 "type": "text", "default": ""}
            ]
        })
    """
    _cleanup_stale()

    questions = args.get("questions", [])
    if not questions:
        return {"error": "No questions provided"}

    title = args.get("title", "I need your input")
    context = args.get("context", "")

    request_id = str(uuid.uuid4())
    event = threading.Event()
    event._created_at = time.time()
    response_holder: dict = {}
    _pending_requests[request_id] = (event, response_holder)

    # Send the questionnaire over WebSocket via the backend's HTTP endpoint.
    payload = {
        "type": "user_questionnaire",
        "request_id": request_id,
        "title": title,
        "context": context,
        "questions": questions,
    }

    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/broadcast_questionnaire",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        _pending_requests.pop(request_id, None)
        return {"error": f"Failed to send questionnaire to GUI: {e}"}

    # Block until the user responds (5 minute timeout).
    if not event.wait(timeout=300):
        _pending_requests.pop(request_id, None)
        return {"error": "Timed out waiting for user response (5 minute limit)"}

    _pending_requests.pop(request_id, None)

    # If the response holder is empty, the user likely closed the card.
    if not response_holder:
        return {"error": "No response received from user"}

    return dict(response_holder)

