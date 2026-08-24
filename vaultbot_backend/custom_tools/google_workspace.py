"""Google Workspace integration tool for VaultBot.

Provides OAuth-authenticated access to Google Calendar, Tasks, and Docs.
Tokens are stored in google_workspace_tokens.json and auto-refreshed.
"""

import base64
import contextlib
import hashlib
import json
import secrets
import webbrowser
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

CONFIG_PATH = Path(__file__).parent.parent / "google_workspace_config.json"
TOKEN_PATH = Path(__file__).parent / "google_workspace_tokens.json"

# In-flight OAuth state. Maps the `state` value sent to Google to the PKCE
# `code_verifier` that must accompany the token exchange. This defeats
# login-CSRF / token-injection attacks on the localhost redirect: a callback
# whose `state` was never issued by us is rejected outright.
#
# Stored in-memory (single-user localhost backend). If the backend restarts
# mid-flow, the pending state is lost and the user simply re-runs 'auth'.
_PENDING_STATES: dict[str, str] = {}


def _b64url(data: bytes) -> str:
    """Base64url-encode bytes (no padding), per RFC 7636."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


SCHEMA = {
    "name": "google_workspace",
    "description": (
        "Interact with Google Workspace APIs (Calendar, Tasks, Docs). "
        "Requires one-time OAuth setup: call 'setup' with client_id and "
        "client_secret, then 'auth' to get a browser sign-in URL, then "
        "'callback' with the auth code. After that, calendar/tasks/docs "
        "actions work with stored tokens (auto-refreshed)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "setup",
                    "auth",
                    "callback",
                    "calendar_list",
                    "calendar_create",
                    "calendar_update",
                    "calendar_delete",
                    "calendar_freebusy",
                    "tasks_list",
                    "tasks_create",
                    "docs_get",
                    "docs_create",
                    "status",
                ],
                "description": "The action to perform.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max events to return (calendar_list).",
            },
            "calendar_id": {
                "type": "string",
                "description": "Calendar ID (defaults to 'primary').",
            },
            "date": {
                "type": "string",
                "description": (
                    "A specific date (YYYY-MM-DD) to list events for "
                    "(calendar_list). Sets time_min to 00:00 and time_max to "
                    "23:59 in local timezone. Takes precedence over "
                    "time_min/time_max."
                ),
            },
            "time_min": {
                "type": "string",
                "description": (
                    "ISO 8601 datetime — only return events starting after "
                    "this (calendar_list). Defaults to now."
                ),
            },
            "time_max": {
                "type": "string",
                "description": (
                    "ISO 8601 datetime — only return events starting before "
                    "this (calendar_list). Optional upper bound."
                ),
            },
            "start": {
                "type": "string",
                "description": "Event start time ISO 8601 (calendar_create).",
            },
            "end": {
                "type": "string",
                "description": "Event end time ISO 8601 (calendar_create).",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Event summary/title (calendar_create, calendar_update)."
                ),
            },
            "event_id": {
                "type": "string",
                "description": (
                    "Google Calendar event ID (calendar_update, calendar_delete)."
                ),
            },
            "location": {
                "type": "string",
                "description": "Event location (calendar_create, calendar_update).",
            },
            "description": {
                "type": "string",
                "description": "Event description (calendar_create, calendar_update).",
            },
            "recurrence": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "RFC5545 RRULE list for recurring events, e.g. "
                    "['RRULE:FREQ=WEEKLY;COUNT=8'] (calendar_create, calendar_update)."
                ),
            },
            "reminders": {
                "type": "object",
                "description": (
                    "Reminder config (calendar_create, calendar_update). "
                    "Use {'useDefault': false, 'overrides': "
                    "[{'method':'popup','minutes':60}]}."
                ),
            },
            "allow_conflicts": {
                "type": "boolean",
                "description": (
                    "If true, calendar_create/update proceeds even "
                    "when overlap is detected."
                ),
            },
            "tasklist_id": {
                "type": "string",
                "description": "Task list ID (tasks_list, tasks_create).",
            },
            "notes": {"type": "string", "description": "Task notes (tasks_create)."},
            "due": {
                "type": "string",
                "description": "Task due date ISO 8601 (tasks_create).",
            },
            "document_id": {
                "type": "string",
                "description": "Google Doc ID (docs_get).",
            },
            "title": {
                "type": "string",
                "description": "Doc title (docs_create) or task title.",
            },
            "client_id": {
                "type": "string",
                "description": "OAuth client ID (for 'setup' action).",
            },
            "client_secret": {
                "type": "string",
                "description": "OAuth client secret (for 'setup' action).",
            },
            "code": {
                "type": "string",
                "description": (
                    "Authorization code from Google redirect (for 'callback' action)."
                ),
            },
        },
        "required": ["action"],
    },
}

REDIRECT_URI = "http://localhost:8000/callback"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/documents",
]


def _load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def _save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _load_tokens():
    if TOKEN_PATH.exists():
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    return None


def _save_tokens(tokens):
    TOKEN_PATH.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _get_credentials():
    cfg = _load_config()
    return cfg.get("client_id"), cfg.get("client_secret")


def _refresh_tokens():
    """Refresh access token using refresh token."""
    import urllib.request

    tokens = _load_tokens()
    if not tokens or "refresh_token" not in tokens:
        return None

    client_id, client_secret = _get_credentials()
    if not client_id:
        return None

    data = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        method="POST",
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            new_tokens = json.loads(resp.read())
            # Preserve refresh_token (not always returned on refresh)
            new_tokens["refresh_token"] = tokens["refresh_token"]
            new_tokens["obtained_at"] = datetime.now(UTC).isoformat()
            _save_tokens(new_tokens)
            return new_tokens
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
    ) as e:
        return {"error": f"Token refresh failed: {e}"}


def _get_access_token():
    """Get a valid access token, refreshing if necessary."""
    tokens = _load_tokens()
    if not tokens:
        return None, "No tokens stored. Run 'auth' action first."

    # Check if token is expired
    obtained = tokens.get("obtained_at")
    expires_in = tokens.get("expires_in", 3600)
    if obtained:
        try:
            obt_time = datetime.fromisoformat(obtained)
            elapsed = (datetime.now(UTC) - obt_time).total_seconds()
            if elapsed >= expires_in - 60:  # Refresh 60s before expiry
                refreshed = _refresh_tokens()
                if refreshed and "access_token" in refreshed:
                    return refreshed["access_token"], None
                elif refreshed and "error" in refreshed:
                    return None, refreshed["error"]
        except Exception:  # noqa: BLE001 — best-effort: malformed timestamp falls back to existing token
            pass

    return tokens.get("access_token"), None


def _api_request(method, url, token, data=None):
    """Make an authenticated Google API request."""
    import urllib.request

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {error_body}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {"error": str(e)}


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_recurrence(raw):
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, None
        return [s], None
    if isinstance(raw, list):
        vals = [str(x).strip() for x in raw if str(x).strip()]
        if not vals:
            return None, None
        return vals, None
    return None, "recurrence must be a string or list of strings"


def _normalize_reminders(raw):
    if raw is None or raw == "":
        return None, None
    if not isinstance(raw, dict):
        return None, "reminders must be an object"

    out = {}
    if "useDefault" in raw:
        out["useDefault"] = bool(raw.get("useDefault"))

    if "overrides" in raw:
        overrides = raw.get("overrides")
        if not isinstance(overrides, list):
            return None, "reminders.overrides must be a list"
        norm_overrides = []
        for item in overrides:
            if not isinstance(item, dict):
                return None, "each reminders override must be an object"
            method = str(item.get("method", "popup")).strip() or "popup"
            minutes = item.get("minutes")
            if not isinstance(minutes, int):
                return None, "reminders override minutes must be an integer"
            norm_overrides.append({"method": method, "minutes": minutes})
        out["overrides"] = norm_overrides

    if not out:
        return None, "reminders object is empty"
    if "overrides" in out and "useDefault" not in out:
        out["useDefault"] = False
    return out, None


def _list_event_conflicts(token, calendar_id, start, end, ignore_event_id=""):
    params = urlencode(
        {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": start,
            "timeMax": end,
            "maxResults": 50,
        }
    )
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events?{params}"
    result = _api_request("GET", url, token)
    if "error" in result:
        return {"error": result["error"]}

    conflicts = []
    for ev in result.get("items", []):
        if ev.get("id") == ignore_event_id:
            continue
        conflicts.append(
            {
                "id": ev.get("id", ""),
                "summary": ev.get("summary", "(no title)"),
                "start": ev.get("start", {}).get(
                    "dateTime", ev.get("start", {}).get("date", "")
                ),
                "end": ev.get("end", {}).get(
                    "dateTime", ev.get("end", {}).get("date", "")
                ),
            }
        )
    return {"conflicts": conflicts}


def run(args: dict) -> dict:
    """Main entry point for the google_workspace tool."""
    action = args.get("action", "")

    # Reject unknown actions up front so a typo doesn't fall through to the
    # auth gate and return a misleading "No tokens stored" error.
    if action not in SCHEMA["parameters"]["properties"]["action"]["enum"]:
        return {"error": f"Unknown action: {action}"}

    # --- Setup: store OAuth credentials ---
    if action == "setup":
        client_id = args.get("client_id", "")
        client_secret = args.get("client_secret", "")
        if not client_id or not client_secret:
            return {"error": "client_id and client_secret are required for setup"}
        cfg = _load_config()
        cfg["client_id"] = client_id
        cfg["client_secret"] = client_secret
        _save_config(cfg)
        return {"status": "ok", "message": "Credentials saved."}

    # --- Auth: start OAuth flow ---
    if action == "auth":
        client_id, client_secret = _get_credentials()
        if not client_id:
            return {"error": "No credentials configured. Run 'setup' first."}

        # Generate a fresh state + PKCE verifier/challenge for this flow.
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
        _PENDING_STATES[state] = code_verifier

        params = {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

        with contextlib.suppress(Exception):
            webbrowser.open(auth_url)

        return {
            "status": "auth_started",
            "auth_url": auth_url,
            "message": (
                "Open this URL to sign in. After consent, Google will "
                "redirect to localhost:8000/callback."
            ),
        }

    # --- Callback: exchange auth code for tokens ---
    if action == "callback":
        import urllib.request

        code = args.get("code", "")
        if not code:
            return {"error": "Authorization code is required for callback."}

        # Validate the OAuth `state` to defeat login-CSRF / token injection.
        # A callback whose state was never issued by our 'auth' action is
        # rejected before any token exchange happens.
        state = args.get("state", "")
        code_verifier = _PENDING_STATES.pop(state, None)
        if not state or code_verifier is None:
            return {"error": "Invalid or missing OAuth state. Re-run 'auth'."}

        client_id, client_secret = _get_credentials()
        if not client_id:
            return {"error": "No credentials configured."}

        data = urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            }
        ).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                tokens = json.loads(resp.read())
                tokens["obtained_at"] = datetime.now(UTC).isoformat()
                _save_tokens(tokens)
                return {"status": "ok", "message": "Tokens saved successfully."}
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
        ) as e:
            return {"error": f"Token exchange failed: {e}"}

    # --- Status: check auth state ---
    if action == "status":
        cfg = _load_config()
        tokens = _load_tokens()
        configured = bool(cfg.get("client_id"))
        authenticated = bool(tokens and tokens.get("access_token"))

        # Check token expiry
        token_expires = None
        if tokens and tokens.get("obtained_at"):
            try:
                obt_time = datetime.fromisoformat(tokens["obtained_at"])
                expires_in = tokens.get("expires_in", 3600)
                token_expires = obt_time.timestamp() + expires_in
            except Exception:  # noqa: BLE001 — best-effort: malformed timestamp leaves token_expires None
                pass

        return {
            "configured": configured,
            "authenticated": authenticated,
            "token_expires_at": token_expires,
            "scopes": " ".join(tokens.get("scope", "").split()) if tokens else "",
        }

    # --- All other actions require authentication ---
    token, err = _get_access_token()
    if err:
        return {"error": err}
    if not token:
        return {"error": "Not authenticated. Run 'auth' action first."}
    # --- Calendar: list events ---
    if action == "calendar_list":
        # If a specific date is provided, compute local-midnight boundaries
        # for that date so the LLM doesn't have to do timezone math (which
        # causes off-by-one errors when UTC vs local time crosses midnight).
        date = args.get("date", "")
        time_min = args.get("time_min", "")
        time_max = args.get("time_max", "")

        if date:
            # Parse YYYY-MM-DD and set boundaries in LOCAL time
            from datetime import time as dt_time

            parsed = datetime.strptime(date, "%Y-%m-%d")
            local_start = datetime.combine(parsed.date(), dt_time.min)  # 00:00 local
            local_end = datetime.combine(
                parsed.date(), dt_time.max
            )  # 23:59:59.999999 local
            # Convert to UTC for the API
            import time as _time

            # Get local UTC offset
            utc_offset = _time.timezone if _time.daylight == 0 else _time.altzone
            tz = timezone(timedelta(seconds=-utc_offset))
            local_start = local_start.replace(tzinfo=tz)
            local_end = local_end.replace(tzinfo=tz)
            time_min = local_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            time_max = local_end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not time_min:
            time_min = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        max_results = args.get("max_results", 10)
        calendar_id = args.get("calendar_id", "primary")
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
            f"?maxResults={max_results}"
            f"&timeMin={time_min}"
            f"&singleEvents=true"
            f"&orderBy=startTime"
        )
        if time_max:
            url += f"&timeMax={time_max}"
        result = _api_request("GET", url, token)
        if "error" in result:
            return result
        events = []
        for ev in result.get("items", []):
            start = ev.get("start", {}).get(
                "dateTime", ev.get("start", {}).get("date", "")
            )
            end = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", ""))
            events.append(
                {
                    "id": ev.get("id", ""),
                    "summary": ev.get("summary", "(no title)"),
                    "start": start,
                    "end": end,
                    "location": ev.get("location", ""),
                    "description": ev.get("description", ""),
                    "recurrence": ev.get("recurrence", []),
                    "recurring_event_id": ev.get("recurringEventId", ""),
                    "reminders": ev.get("reminders", {}),
                }
            )
        return {"events": events}
    # --- Calendar: create event ---
    if action == "calendar_create":
        summary = args.get("summary", "")
        start = args.get("start", "")
        end = args.get("end", "")
        calendar_id = args.get("calendar_id", "primary")
        if not summary or not start or not end:
            return {
                "error": "summary, start, and end are required for calendar_create."
            }

        allow_conflicts = _as_bool(args.get("allow_conflicts"), default=False)
        if not allow_conflicts:
            conflict_check = _list_event_conflicts(token, calendar_id, start, end)
            if "error" in conflict_check:
                return {"error": f"Conflict check failed: {conflict_check['error']}"}
            conflicts = conflict_check.get("conflicts", [])
            if conflicts:
                return {
                    "error": (
                        "Time conflict detected. "
                        "Set allow_conflicts=true to force create."
                    ),
                    "conflicts": conflicts,
                }

        event_data = {
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if args.get("location"):
            event_data["location"] = args["location"]
        if args.get("description"):
            event_data["description"] = args["description"]
        recurrence, recurrence_err = _normalize_recurrence(args.get("recurrence"))
        if recurrence_err:
            return {"error": recurrence_err}
        if recurrence:
            event_data["recurrence"] = recurrence
        reminders, reminders_err = _normalize_reminders(args.get("reminders"))
        if reminders_err:
            return {"error": reminders_err}
        if reminders:
            event_data["reminders"] = reminders

        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        result = _api_request("POST", url, token, event_data)
        return result

    # --- Calendar: update event ---
    if action == "calendar_update":
        event_id = args.get("event_id", "")
        if not event_id:
            return {"error": "event_id is required for calendar_update."}

        calendar_id = args.get("calendar_id", "primary")
        patch_data = {}

        for key in ("summary", "location", "description"):
            if key in args and args.get(key) is not None:
                patch_data[key] = args.get(key)

        if args.get("start"):
            patch_data["start"] = {"dateTime": args.get("start")}
        if args.get("end"):
            patch_data["end"] = {"dateTime": args.get("end")}

        recurrence, recurrence_err = _normalize_recurrence(args.get("recurrence"))
        if recurrence_err:
            return {"error": recurrence_err}
        if recurrence is not None:
            patch_data["recurrence"] = recurrence

        reminders, reminders_err = _normalize_reminders(args.get("reminders"))
        if reminders_err:
            return {"error": reminders_err}
        if reminders is not None:
            patch_data["reminders"] = reminders

        if not patch_data:
            return {
                "error": (
                    "No update fields provided. Include one of summary, start, end, "
                    "location, description, recurrence, or reminders."
                )
            }

        allow_conflicts = _as_bool(args.get("allow_conflicts"), default=False)
        if not allow_conflicts and args.get("start") and args.get("end"):
            conflict_check = _list_event_conflicts(
                token,
                calendar_id,
                args.get("start"),
                args.get("end"),
                ignore_event_id=event_id,
            )
            if "error" in conflict_check:
                return {"error": f"Conflict check failed: {conflict_check['error']}"}
            conflicts = conflict_check.get("conflicts", [])
            if conflicts:
                return {
                    "error": (
                        "Time conflict detected. "
                        "Set allow_conflicts=true to force update."
                    ),
                    "conflicts": conflicts,
                }

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/"
            f"{event_id}"
        )
        return _api_request("PATCH", url, token, patch_data)

    # --- Calendar: delete event ---
    if action == "calendar_delete":
        event_id = args.get("event_id", "")
        if not event_id:
            return {"error": "event_id is required for calendar_delete."}
        calendar_id = args.get("calendar_id", "primary")
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/"
            f"{event_id}"
        )
        result = _api_request("DELETE", url, token)
        if isinstance(result, dict) and "error" in result:
            return result
        return {"status": "deleted", "event_id": event_id, "calendar_id": calendar_id}

    # --- Calendar: free/busy windows ---
    if action == "calendar_freebusy":
        calendar_id = args.get("calendar_id", "primary")
        date = args.get("date", "")
        time_min = args.get("time_min", "")
        time_max = args.get("time_max", "")

        if date and (not time_min or not time_max):
            import time as _time
            from datetime import time as dt_time

            parsed = datetime.strptime(date, "%Y-%m-%d")
            local_start = datetime.combine(parsed.date(), dt_time.min)
            local_end = datetime.combine(parsed.date(), dt_time.max)
            utc_offset = _time.timezone if _time.daylight == 0 else _time.altzone
            tz = timezone(timedelta(seconds=-utc_offset))
            time_min = (
                local_start.replace(tzinfo=tz)
                .astimezone(UTC)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            time_max = (
                local_end.replace(tzinfo=tz)
                .astimezone(UTC)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )

        if not time_min or not time_max:
            return {
                "error": "time_min and time_max are required for calendar_freebusy."
            }

        payload = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": calendar_id}],
        }
        url = "https://www.googleapis.com/calendar/v3/freeBusy"
        result = _api_request("POST", url, token, payload)
        if "error" in result:
            return result

        busy = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return {
            "calendar_id": calendar_id,
            "time_min": time_min,
            "time_max": time_max,
            "busy": busy,
        }

    # --- Tasks: list task lists or tasks ---
    if action == "tasks_list":
        tasklist_id = args.get("tasklist_id", "")
        if not tasklist_id:
            # List all task lists
            url = "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
            result = _api_request("GET", url, token)
            if "error" in result:
                return result
            lists = []
            for tl in result.get("items", []):
                lists.append(
                    {
                        "id": tl.get("id", ""),
                        "title": tl.get("title", ""),
                    }
                )
            return {"task_lists": lists}
        else:
            # List tasks in a specific task list
            url = f"https://tasks.googleapis.com/tasks/v1/lists/{tasklist_id}/tasks"
            result = _api_request("GET", url, token)
            if "error" in result:
                return result
            tasks = []
            for t in result.get("items", []):
                tasks.append(
                    {
                        "id": t.get("id", ""),
                        "title": t.get("title", ""),
                        "status": t.get("status", ""),
                        "due": t.get("due", ""),
                        "notes": t.get("notes", ""),
                    }
                )
            return {"tasks": tasks}

    # --- Tasks: create task ---
    if action == "tasks_create":
        tasklist_id = args.get("tasklist_id", "")
        title = args.get("title", args.get("summary", ""))
        if not title:
            return {"error": "title is required for tasks_create."}
        if not tasklist_id:
            # Use the first task list
            url = "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
            result = _api_request("GET", url, token)
            if "error" in result:
                return result
            items = result.get("items", [])
            if not items:
                return {
                    "error": "No task lists found. Create one in Google Tasks first."
                }
            tasklist_id = items[0].get("id", "")

        task_data = {"title": title}
        if args.get("notes"):
            task_data["notes"] = args["notes"]
        if args.get("due"):
            task_data["due"] = args["due"]

        url = f"https://tasks.googleapis.com/tasks/v1/lists/{tasklist_id}/tasks"
        result = _api_request("POST", url, token, task_data)
        return result

    # --- Docs: get document ---
    if action == "docs_get":
        document_id = args.get("document_id", "")
        if not document_id:
            return {"error": "document_id is required for docs_get."}
        url = f"https://docs.googleapis.com/v1/documents/{document_id}"
        result = _api_request("GET", url, token)
        if "error" in result:
            return result
        # Extract text content
        content = ""
        for elem in result.get("body", {}).get("content", []):
            for para in elem.get("paragraph", {}).get("elements", []):
                content += para.get("textRun", {}).get("content", "")
        return {
            "title": result.get("title", ""),
            "document_id": result.get("documentId", ""),
            "content": content,
        }

    # --- Docs: create document ---
    if action == "docs_create":
        title = args.get("title", "")
        if not title:
            return {"error": "title is required for docs_create."}
        url = "https://docs.googleapis.com/v1/documents"
        result = _api_request("POST", url, token, {"title": title})
        return result
