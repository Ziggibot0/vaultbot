"""
Agent-authored tool: ask_user
"""

SCHEMA = {
    "name": "ask_user",
    "description": "Send an interactive questionnaire to the user via the Obsidian plugin GUI and block until the user responds. The user can answer each question, pick \"I don't know\" for any question, and add free-text comments for nuance. Use this when you need the user's input to crystallize an idea or make a decision.",
    "parameters": {
        "properties": {
            "context": {
                "description": "Background context explaining what you're trying to decide or what information you need",
                "type": "string",
            },
            "questions": {
                "description": "List of simple question objects. Each has 'question' (the text to display) and optionally 'options' (a list of strings — if present, the question is single-choice radio; if absent, it's free-form text). Optionally 'id' (a unique key for the answer; auto-generated as q1, q2... if omitted).",
                "items": {
                    "properties": {
                        "id": {
                            "description": "Unique key for this question (used as the key in the returned dict). Auto-generated as q1, q2... if omitted.",
                            "type": "string",
                        },
                        "options": {
                            "description": "List of option strings for single-choice. If present, the question is radio (pick one). If absent, the question is free-form text.",
                            "items": {"type": "string"},
                            "type": "array",
                        },
                        "question": {
                            "description": "The question text to display",
                            "type": "string",
                        },
                    },
                    "required": ["question"],
                    "type": "object",
                },
                "type": "array",
            },
            "title": {
                "description": "Short title for the question card (e.g. 'Research approach')",
                "type": "string",
            },
        },
        "required": ["title", "questions"],
        "type": "object",
    },
}


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

# Module-level registry: request_id -> (event, response_dict, websocket_ref)
# The /user_response HTTP endpoint reads this to unblock the waiting tool.
# websocket_ref is used by /broadcast_questionnaire to send the
# questionnaire to the owning tab only (not broadcast to all tabs).  May be
# None for back-compat (legacy callers that don't pass websocket=).
_pending_requests: dict[str, tuple[threading.Event, dict, object]] = {}


def _cleanup_stale():
    """Remove requests older than 10 minutes (defensive cleanup)."""
    now = time.time()
    stale = []
    for rid, entry in list(_pending_requests.items()):
        ev = entry[0]
        if (
            not ev.is_set()
            and hasattr(ev, "_created_at")
            and (now - ev._created_at) > 600
        ):
            stale.append(rid)
    for rid in stale:
        try:
            entry = _pending_requests.pop(rid)
            entry[0].set()  # unblock with error
        except KeyError:
            pass


def run(
    args: dict, websocket: object | None = None, session_id: str | None = None
) -> dict:
    """Send a questionnaire to the user and wait for their response.

    Args:
        title: Short title for the question card (e.g. "Research approach")
        context: Background context explaining what you're trying to decide
        questions: List of simplified question objects, each with:
            - question: the question text (required)
            - options: list of option strings (optional — if present,
              the question is single-choice radio; if absent, free-form text)
            - id: unique key for this question (optional — auto-generated
              as q1, q2... if omitted)
        websocket: The owning WebSocket connection (optional).  When
            provided, the questionnaire is sent to THIS tab only, not
            broadcast to all tabs.  This enables multi-tab isolation.
        session_id: The session_id of the owning tab (optional, for logging).

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
                 "options": ["Typed", "Untyped", "Hybrid"]},
                {"id": "nuance", "question": "Any constraints?"}
            ]
        }, websocket=ws, session_id=session_id)
    """
    _cleanup_stale()

    raw_questions = args.get("questions", [])
    if not raw_questions:
        return {"error": "No questions provided"}

    title = args.get("title", "I need your input")
    context = args.get("context", "")

    # Normalize simplified schema → full GUI-compatible format.
    # The GUI expects each question to have: id, question, type, options,
    # and default. We derive type from options presence and auto-generate
    # ids if the LLM didn't provide them.
    questions = []
    for i, q in enumerate(raw_questions):
        q_id = q.get("id") or f"q{i + 1}"
        q_text = q.get("question", "")
        q_options = q.get("options")
        if q_options and isinstance(q_options, list) and len(q_options) > 0:
            q_type = "radio"
            q_default = "best_practices"  # pre-select "I don't know" by default
        else:
            q_type = "text"
            q_default = q.get("default", "") if isinstance(q.get("default"), str) else ""
            q_options = None
        gui_q = {
            "id": q_id,
            "question": q_text,
            "type": q_type,
        }
        if q_options:
            gui_q["options"] = q_options
        gui_q["default"] = q_default
        questions.append(gui_q)

    request_id = str(uuid.uuid4())
    event = threading.Event()
    event._created_at = time.time()
    response_holder: dict = {}
    _pending_requests[request_id] = (event, response_holder, websocket)

    # Send the questionnaire over WebSocket via the backend's HTTP endpoint.
    payload = {
        "type": "user_questionnaire",
        "request_id": request_id,
        "title": title,
        "context": context,
        "questions": questions,
        "session_id": session_id,
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
