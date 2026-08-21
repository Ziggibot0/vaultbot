"""
Pattern Extractor — deterministic extraction of cross-session patterns from
episodic memory (chat logs, calibration log, procedure failure log).

Pure deterministic. No LLM calls. Scans Memory/Chat/ logs, extracts
structured patterns (recurring topics, sentiment, tool usage, workflows,
over-reporting, self-model drift), and returns them for consolidation.

The consolidation pipeline works as follows:
  1. Pattern extraction (this module) — deterministic, no LLM
  2. Pattern clustering — graph analysis, no LLM
  3. Semantic note synthesis — LLM-assisted but scaffolded
  4. Quality validation — deterministic (vault_lint, claim_verifier)

This module handles step 1. It produces structured JSON findings that
step 2-3 can consume.

See [[Semantic-Consolidation-Architecture]] for the full design.
See [[How-to-Consolidate-Experiences-into-Semantic-Knowledge]] for the procedure.
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

# --- Constants ---

# Only match actual timestamp headers, not markdown ## section headers
# inside assistant responses. Timestamps look like "2026-07-25 20:53 UTC".
_TIMESTAMP_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?:\s*UTC)?)\s*$", re.MULTILINE
)

# Tool names to detect in chat log text
_TOOL_NAMES = {
    "vault_research",
    "vault_search",
    "vault_lint",
    "vault_list",
    "vault_append",
    "vault_delete",
    "code_run",
    "code_read",
    "tool_create",
    "safe_write",
    "textbook_ingest",
    "textbook_read_page",
    "vault_gaps",
    "vault_graph_analyzer",
    "self_reflect",
    "capability_audit",
    "preflight_safety_check",
    "git_rollback",
    "web_read_source",
    "vaultbot_status",
}

# Sentiment keywords for detecting the operator's response sentiment
_POSITIVE_KW = {
    "yes",
    "go ahead",
    "cool",
    "nice",
    "good",
    "great",
    "like",
    "love",
    "proceed",
    "begin",
    "please do",
    "definitely",
    "yeah",
    "yea",
    "beans",
    "go for it",
    "please",
    "exactly",
    "perfect",
    "awesome",
    "sweet",
    "agree",
    "right",
    "correct",
}
_NEGATIVE_KW = {
    "no",
    "wrong",
    "fix",
    "didn't",
    "didnt",
    "lagging",
    "junk",
    "stale",
    "break",
    "broke",
    "not convinced",
    "don't trust",
    "huge",
    "didn't read",
    "too much",
    "i thought you already",
    "sync yourself",
    "dinosaur",
    "empty files",
    "haven't",
    "not what i",
    "are you sure",
    "double check",
    "make sure",
}

# Thresholds
_MIN_SESSIONS_FOR_PATTERN = 3  # wikilink must appear in 3+ sessions
_OVER_REPORT_THRESHOLD = 2000  # assistant message > 2000 chars = over-reporting
_MAX_LOG_ENTRIES = 100  # keep consolidation log bounded


class PatternExtractor:
    """Extracts cross-session patterns from episodic memory sources.

    Scans chat logs in Memory/Chat/ and extracts:
      - Recurring topics (wikilinks appearing in multiple sessions)
      - Sentiment patterns (the operator's positive/negative/neutral responses)
      - Tool usage frequency and co-occurrence
      - Workflow patterns (common tool sequences)
      - Over-reporting detection (excessively long assistant responses)
      - Self-model drift (historical — SELF_MODEL.md was removed 2026-08-13)

    All extraction is deterministic — no LLM calls. The output is structured
    JSON that the consolidation pipeline (clustering + LLM synthesis) consumes.
    """

    def __init__(self, vault_path: str | None = None, log_path: str | None = None):
        self.vault_path = vault_path or os.getenv("VAULT_PATH", ".")
        self.chat_dir = os.path.join(self.vault_path, "vaultbot-stuff/Memory/Chat")
        self.log_path = log_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "consolidation_log.json"
        )
        self._ensure_log()

    def _ensure_log(self):
        """Create the consolidation log file if it doesn't exist."""
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"consolidations": [], "last_consolidation": None}, f, indent=2
                )

    def _load(self) -> dict:
        with open(self.log_path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict):
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # --- Chat log parsing ---

    def _parse_chat_log(self, filepath: str) -> dict:
        """Parse a single chat log into structured exchanges.

        Returns:
            {
                'title': str,
                'file': str (filename),
                'exchanges': [
                    {
                        'timestamp': str,
                        'user_message': str (truncated to 500 chars),
                        'assistant_length': int,
                        'wikilinks': [str],
                        'tools_mentioned': [str],
                        'sentiment': 'positive' | 'negative' | 'neutral',
                    }
                ]
            }
        """
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
        except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            return {"title": "", "file": os.path.basename(filepath), "exchanges": []}

        title_match = re.match(r"^# Chat:\s*(.+)$", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else Path(filepath).stem

        timestamps = [
            (m.start(), m.group(1).strip()) for m in _TIMESTAMP_RE.finditer(content)
        ]

        exchanges = []
        for i, (start, ts) in enumerate(timestamps):
            end = timestamps[i + 1][0] if i + 1 < len(timestamps) else len(content)
            section = content[start:end]

            # Extract user message
            user_match = re.search(
                r"\*\*User:\*\*\s*(.+?)(?:\n\n\*\*Assistant|\Z)", section, re.DOTALL
            )
            user_msg = user_match.group(1).strip() if user_match else ""

            # Extract assistant message (up to thinking block or end)
            asst_match = re.search(
                r"\*\*Assistant:\*\*\s*(.+?)(?:<details>|\Z)", section, re.DOTALL
            )
            asst_msg = asst_match.group(1).strip() if asst_match else ""

            # Extract thinking
            think_match = re.search(
                r"<summary>Thinking process</summary>\s*(.+?)(?:</details>|\Z)",
                section,
                re.DOTALL,
            )
            thinking = think_match.group(1).strip() if think_match else ""

            # Wikilinks from the full section
            wikilinks = re.findall(r"\[\[([^\]]+)\]\]", section)
            wikilinks = [link.split("|")[0].split("#")[0].strip() for link in wikilinks]

            # Tool mentions from full exchange text
            all_text = asst_msg + " " + thinking
            tools_mentioned = sorted(tool for tool in _TOOL_NAMES if tool in all_text)

            # Sentiment detection from user message
            user_lower = user_msg.lower()
            sentiment = "neutral"
            for kw in _POSITIVE_KW:
                if kw in user_lower:
                    sentiment = "positive"
                    break
            if sentiment == "neutral":
                for kw in _NEGATIVE_KW:
                    if kw in user_lower:
                        sentiment = "negative"
                        break

            exchanges.append(
                {
                    "timestamp": ts,
                    "user_message": user_msg[:500],
                    "assistant_length": len(asst_msg),
                    "wikilinks": wikilinks,
                    "tools_mentioned": tools_mentioned,
                    "sentiment": sentiment,
                }
            )

        return {
            "title": title,
            "file": os.path.basename(filepath),
            "exchanges": exchanges,
        }

    def scan_chat_logs(self, since_timestamp: str | None = None) -> list[dict]:
        """Scan all chat logs and return structured sessions.

        Args:
            since_timestamp: If provided, only return exchanges after this
                ISO timestamp. Used for incremental consolidation.

        Returns:
            List of session dicts, each with 'title', 'file', 'exchanges'.
        """
        if not os.path.isdir(self.chat_dir):
            return []

        chat_files = sorted(f for f in os.listdir(self.chat_dir) if f.endswith(".md"))

        sessions = []
        for cf in chat_files:
            session = self._parse_chat_log(os.path.join(self.chat_dir, cf))

            # Filter by timestamp if requested
            if since_timestamp and session["exchanges"]:
                filtered = [
                    ex
                    for ex in session["exchanges"]
                    if ex["timestamp"] > since_timestamp
                ]
                if not filtered:
                    continue
                session["exchanges"] = filtered

            sessions.append(session)

        return sessions

    # --- Pattern extractors ---

    def extract_recurring_topics(self, sessions: list[dict]) -> list[dict]:
        """Find wikilinks that appear in 3+ sessions.

        These are the topics that keep coming up — candidates for semantic
        consolidation into reusable knowledge.
        """
        wikilink_sessions = defaultdict(set)
        wikilink_counts = Counter()

        for session in sessions:
            file_name = session["file"]
            session_links = set()
            for ex in session["exchanges"]:
                for link in ex["wikilinks"]:
                    session_links.add(link)
                    wikilink_counts[link] += 1
            for link in session_links:
                wikilink_sessions[link].add(file_name)

        recurring = []
        for link, sess_set in wikilink_sessions.items():
            if len(sess_set) >= _MIN_SESSIONS_FOR_PATTERN:
                recurring.append(
                    {
                        "topic": link,
                        "session_count": len(sess_set),
                        "total_mentions": wikilink_counts[link],
                        "sessions": sorted(sess_set),
                    }
                )

        recurring.sort(key=lambda x: -x["session_count"])
        return recurring

    def extract_sentiment_patterns(self, sessions: list[dict]) -> dict:
        """Extract the operator's response sentiment distribution and
        negative exchanges.

        The negative rate is a key calibration metric: if it's not trending
        down over time, the consolidation system isn't working.
        """
        all_exchanges = [ex for s in sessions for ex in s["exchanges"]]
        sentiment_counts = Counter(ex["sentiment"] for ex in all_exchanges)
        total = len(all_exchanges)

        negative_exchanges = [
            {
                "timestamp": ex["timestamp"],
                "user_message": ex["user_message"][:150],
                "file": s["file"],
            }
            for s in sessions
            for ex in s["exchanges"]
            if ex["sentiment"] == "negative"
        ]

        return {
            "total_exchanges": total,
            "distribution": dict(sentiment_counts),
            "negative_rate": round(sentiment_counts.get("negative", 0) / total, 4)
            if total > 0
            else 0,
            "negative_exchanges": negative_exchanges,
        }

    def extract_tool_patterns(self, sessions: list[dict]) -> dict:
        """Extract tool usage frequency and co-occurrence patterns.

        Tool co-occurrence reveals workflow patterns: which tools are used
        together, indicating standard workflows.
        """
        tool_counter = Counter()
        tool_cooccurrence = defaultdict(int)

        for session in sessions:
            for ex in session["exchanges"]:
                tools = ex["tools_mentioned"]
                for tool in tools:
                    tool_counter[tool] += 1
                # Co-occurrence: pairs of tools in the same exchange
                for i, t1 in enumerate(tools):
                    for t2 in tools[i + 1 :]:
                        pair = tuple(sorted([t1, t2]))
                        tool_cooccurrence[pair] += 1

        # Top co-occurring tool pairs (workflow indicators)
        top_workflows = [
            {"tools": f"{k[0]} + {k[1]}", "count": v}
            for k, v in sorted(tool_cooccurrence.items(), key=lambda x: -x[1])[:10]
        ]

        return {
            "tool_frequency": dict(tool_counter.most_common()),
            "top_workflows": top_workflows,
        }

    def extract_over_reporting(self, sessions: list[dict]) -> dict:
        """Detect exchanges where the assistant response was excessively long.

        the operator's communication preference is bottom-line-up-front. Long
        responses are a pattern to consolidate into a semantic rule.
        """
        long_exchanges = []
        for session in sessions:
            for ex in session["exchanges"]:
                if ex["assistant_length"] > _OVER_REPORT_THRESHOLD:
                    long_exchanges.append(
                        {
                            "timestamp": ex["timestamp"],
                            "file": session["file"],
                            "assistant_length": ex["assistant_length"],
                            "user_message": ex["user_message"][:100],
                        }
                    )

        return {
            "count": len(long_exchanges),
            "threshold_chars": _OVER_REPORT_THRESHOLD,
            "exchanges": long_exchanges,
        }

    def extract_all(self, since_timestamp: str | None = None) -> dict:
        """Run all pattern extractors and return structured findings.

        This is the main entry point for the consolidation pipeline.
        The output is a JSON-serializable dict that can be consumed by
        the clustering and synthesis steps.

        Args:
            since_timestamp: If provided, only scan exchanges after this
                timestamp (for incremental consolidation).

        Returns:
            {
                'timestamp': str,
                'total_sessions': int,
                'total_exchanges': int,
                'recurring_topics': [...],
                'sentiment': {...},
                'tool_patterns': {...},
                'over_reporting': {...},
            }
        """
        sessions = self.scan_chat_logs(since_timestamp)
        all_exchanges = [ex for s in sessions for ex in s["exchanges"]]

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_sessions": len(sessions),
            "total_exchanges": len(all_exchanges),
            "recurring_topics": self.extract_recurring_topics(sessions),
            "sentiment": self.extract_sentiment_patterns(sessions),
            "tool_patterns": self.extract_tool_patterns(sessions),
            "over_reporting": self.extract_over_reporting(sessions),
        }

    # --- Logging ---

    def log_consolidation(self, patterns: dict, note_path: str | None = None) -> dict:
        """Log a consolidation run for tracking.

        Args:
            patterns: The extracted patterns that were consolidated.
            note_path: Path to the semantic note written (if any).

        Returns:
            The log entry that was written.
        """
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "sessions_scanned": patterns.get("total_sessions", 0),
            "exchanges_scanned": patterns.get("total_exchanges", 0),
            "recurring_topics_found": len(patterns.get("recurring_topics", [])),
            "negative_exchanges": patterns.get("sentiment", {})
            .get("distribution", {})
            .get("negative", 0),
            "note_written": note_path,
        }

        data = self._load()
        data["consolidations"].append(entry)
        data["last_consolidation"] = entry["timestamp"]

        # Bound the log
        if len(data["consolidations"]) > _MAX_LOG_ENTRIES:
            data["consolidations"] = data["consolidations"][-_MAX_LOG_ENTRIES:]

        self._save(data)
        return entry

    def get_last_consolidation_time(self) -> str | None:
        """Return the timestamp of the last consolidation run, or None."""
        data = self._load()
        return data.get("last_consolidation")

    # --- Reporting ---

    def consolidation_report(self) -> dict:
        """Return a summary of consolidation history."""
        data = self._load()
        consolidations = data.get("consolidations", [])

        if not consolidations:
            return {"status": "no_data", "message": "No consolidation runs logged yet."}

        return {
            "total_runs": len(consolidations),
            "last_run": consolidations[-1]["timestamp"],
            "total_sessions_scanned": sum(
                c.get("sessions_scanned", 0) for c in consolidations
            ),
            "total_notes_written": sum(
                1 for c in consolidations if c.get("note_written")
            ),
            "recent_runs": consolidations[-5:],
        }

    def get_consolidation_gaps(self) -> list[dict]:
        """Return patterns that need consolidation, for the autonomous researcher.

        This is the interface for the autonomous researcher: it calls this
        method to find out what patterns are ripe for consolidation into
        semantic notes.
        """
        last_run = self.get_last_consolidation_time()
        patterns = self.extract_all(since_timestamp=last_run)

        gaps = []

        # Gap 1: New recurring topics since last consolidation
        for topic in patterns.get("recurring_topics", []):
            gaps.append(
                {
                    "kind": "recurring_topic",
                    "topic": topic["topic"],
                    "priority": topic["session_count"] * 10,
                    "session_count": topic["session_count"],
                    "total_mentions": topic["total_mentions"],
                    "evidence": topic["sessions"],
                    "description": f"Topic '{topic['topic']}' appears in "
                    f"{topic['session_count']} sessions "
                    f"({topic['total_mentions']} mentions). "
                    f"Ready for semantic consolidation.",
                }
            )

        # Gap 2: High negative sentiment rate
        sentiment = patterns.get("sentiment", {})
        if sentiment.get("negative_rate", 0) > 0.25:
            gaps.append(
                {
                    "kind": "high_correction_rate",
                    "topic": "operator-correction-patterns",
                    "priority": int(sentiment["negative_rate"] * 100),
                    "negative_rate": sentiment["negative_rate"],
                    "negative_count": len(sentiment.get("negative_exchanges", [])),
                    "description": f"Negative sentiment rate is "
                    f"{sentiment['negative_rate']:.0%}. "
                    f"Consolidate correction patterns into "
                    f"semantic knowledge to reduce repeat failures.",
                }
            )

        # Gap 3: Over-reporting pattern
        over_reporting = patterns.get("over_reporting", {})
        if over_reporting.get("count", 0) >= 3:
            gaps.append(
                {
                    "kind": "over_reporting",
                    "topic": "communication-brevity",
                    "priority": 40,
                    "count": over_reporting["count"],
                    "description": f"{over_reporting['count']} exchanges exceeded "
                    f"{over_reporting['threshold_chars']} chars. "
                    f"Consolidate into a 'keep it short' rule.",
                }
            )

        # Sort by priority (highest first)
        gaps.sort(key=lambda x: -x.get("priority", 0))
        return gaps
