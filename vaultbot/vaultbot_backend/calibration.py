"""
Calibration Tracker — uses the operator's corrections as ground truth to calibrate
automated quality gates (vault_lint, procedure_tracker, etc.).

Pure deterministic. No LLM calls. Structured logging + simple statistics.

See [[Calibration-via-Operator-Feedback]] for the architecture rationale.
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime


class CalibrationTracker:
    """Tracks operator corrections and quality-gate decisions to compute
    calibration metrics over time.

    the operator's corrections are ground truth. Every time the operator says
    "that's wrong" or "you missed X", that's a labeled data point. We log it
    with full context
    (what was retrieved, what gates passed, what procedures were in context)
    so we can later compute: are the automated gates actually measuring what
    we think they're measuring?
    """

    # --- Correction detection patterns ---

    CORRECTION_PATTERNS = (
        r"\bno\b",
        r"\bwrong\b",
        r"\bactually\b",
        r"\bthat'?s not\b",
        r"\bfix\b",
        r"\bnot right\b",
        r"\bincorrect\b",
        r"\byou missed\b",
        r"\byou forgot\b",
        r"\bnot quite\b",
        r"\btry again\b",
        r"\bthat isn'?t\b",
        r"\bnot what i (asked|meant|wanted)\b",
        r"\bthat'?s incorrect\b",
    )

    # Phrases that look like corrections but aren't (false positive guards)
    FALSE_POSITIVE_PATTERNS = (
        r"\bno worries\b",
        r"\bno problem\b",
        r"\bno rush\b",
        r"\bno really\b",
        r"\bnot sure\b",
        r"\bnot really\b",
        r"\bnot necessarily\b",
        r"\bnot bad\b",
        r"\bnot great\b",
        r"\bnot too bad\b",
        r"\bnot only\b",
        r"\bnot just\b",
        r"\bactually (good|great|nice|cool|yeah|yes)\b",
        r"\bno,?\s*i (think|believe|want|need|mean)\b",
    )

    def __init__(self, log_path: str | None = None):
        self.log_path = log_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "calibration_log.json"
        )
        self._ensure_log()

    def _ensure_log(self):
        """Create the log file if it doesn't exist."""
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump({"corrections": [], "gate_decisions": []}, f, indent=2)

    def _load(self) -> dict:
        with open(self.log_path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict):
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # --- Correction detection ---

    def detect_correction(
        self, user_message: str, prev_answer: str | None = None
    ) -> bool:
        """Heuristic: does this message look like a correction of the previous answer?

        Conservative — false negatives just mean we miss calibration data,
        not that we break anything. Better to miss a correction than to
        log a false positive and pollute the calibration data.
        """
        if not user_message or not prev_answer:
            return False

        msg_lower = user_message.lower().strip()

        # Must be short-ish (corrections are usually brief, not essays)
        if len(msg_lower) > 500:
            return False

        # Check false positives first — if it matches a false positive pattern,
        # it's NOT a correction even if it also matches a correction pattern
        for pattern in self.FALSE_POSITIVE_PATTERNS:
            if re.search(pattern, msg_lower):
                return False

        # Check correction patterns
        for pattern in self.CORRECTION_PATTERNS:
            if re.search(pattern, msg_lower):
                return True

        return False

    def classify_failure(
        self,
        user_message: str,
        prev_answer: str | None = None,
        retrieved_notes: list[str] | None = None,
    ) -> str:
        """Classify the type of failure based on the correction message.

        Returns one of: 'retrieval', 'synthesis', 'verification', 'unknown'
        """
        msg_lower = user_message.lower()

        # Retrieval failure: user says we missed something or didn't answer the question
        if any(
            kw in msg_lower
            for kw in [
                "you missed",
                "you forgot",
                "didn't mention",
                "left out",
                "not what i asked",
                "not what i meant",
                "not what i wanted",
                "where's",
                "where is",
                "you didn't include",
                "you didn't find",
                "missing",
                "didn't find",
            ]
        ):
            return "retrieval"

        # Verification failure: user says something is factually wrong
        if any(
            kw in msg_lower
            for kw in [
                "that's wrong",
                "that's not right",
                "incorrect",
                "not true",
                "false",
                "made up",
                "hallucinat",
                "that's not correct",
                "factually",
                "that's incorrect",
            ]
        ):
            return "verification"

        # Synthesis failure: user says the answer is wrong but not about specific facts
        if any(
            kw in msg_lower
            for kw in [
                "wrong",
                "not right",
                "try again",
                "not quite",
                "that's not",
                "fix this",
                "fix that",
                "fix it",
            ]
        ):
            return "synthesis"

        return "unknown"

    # --- Logging ---

    def log_correction(
        self,
        user_message: str,
        prev_answer: str,
        procedures_in_context: list[str] | None = None,
        validation_results: list[dict] | None = None,
        retrieved_notes: list[str] | None = None,
        failure_type: str | None = None,
    ) -> dict:
        """Log a correction event with full context.

        This is the core data point: the operator corrected an output, and we capture
        everything about the context so we can later compute calibration metrics.
        """
        if failure_type is None:
            failure_type = self.classify_failure(
                user_message, prev_answer, retrieved_notes
            )

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "user_message": user_message[:500],
            "prev_answer": prev_answer[:2000] if prev_answer else "",
            "failure_type": failure_type,
            "procedures_in_context": procedures_in_context or [],
            "validation_results": validation_results or [],
            "retrieved_notes": retrieved_notes or [],
        }

        data = self._load()
        data["corrections"].append(entry)
        self._save(data)

        return entry

    def log_gate_decision(
        self, gate_name: str, note_path: str, decision: str, details: dict | None = None
    ):
        """Log a quality gate decision (pass/fail) for a note.

        This lets us later compute: of the notes the operator corrected,
        how many did the gate pass? (false positives)
        Of the notes the operator approved, how many did the gate fail?
        (false negatives)
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "gate": gate_name,
            "note_path": note_path,
            "decision": decision,
            "details": details or {},
        }

        data = self._load()
        data["gate_decisions"].append(entry)
        self._save(data)

    # --- Reporting ---

    def calibration_report(self) -> dict:
        """Compute calibration metrics for each quality gate.

        For each gate:
        - Total decisions, pass count, fail count, pass rate
        - Cross-reference with corrections (if a corrected note was passed by the gate,
          that's a false positive)

        Full precision/recall requires note-level tracking linking corrections to
        gate decisions. This is the foundation — we log the data now, compute
        metrics as the dataset grows.
        """
        data = self._load()
        corrections = data.get("corrections", [])
        gate_decisions = data.get("gate_decisions", [])

        if not gate_decisions and not corrections:
            return {"status": "no_data", "message": "No calibration data logged yet."}

        # Group gate decisions by gate name
        gates = defaultdict(list)
        for d in gate_decisions:
            gates[d["gate"]].append(d)

        report = {}
        for gate_name, decisions in gates.items():
            total = len(decisions)
            passed = sum(1 for d in decisions if d["decision"] == "pass")
            failed = total - passed

            report[gate_name] = {
                "total_decisions": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / total, 4) if total > 0 else 0,
            }

        # Overall correction stats
        report["_corrections"] = {
            "total": len(corrections),
            "by_type": dict(Counter(c["failure_type"] for c in corrections)),
        }

        return report

    def get_calibration_gaps(self) -> list[dict]:
        """Return gaps for the autonomous researcher: gates with poor calibration.

        A gate has poor calibration if:
        - Pass rate > 95% AND corrections exist → potential false positives
          (gate too lenient)
        - Pass rate < 20% → potential false negatives (gate too strict)

        These gaps feed into the autonomous researcher so it can research
        how to adjust the gate's thresholds.
        """
        report = self.calibration_report()
        if report.get("status") == "no_data":
            return []

        gaps = []
        corrections_count = report.get("_corrections", {}).get("total", 0)

        for gate_name, stats in report.items():
            if gate_name.startswith("_"):
                continue

            pass_rate = stats.get("pass_rate", 0)
            total = stats.get("total_decisions", 0)

            if total < 5:
                continue  # not enough data to draw conclusions

            if pass_rate > 0.95 and corrections_count > 0:
                gaps.append(
                    {
                        "gate": gate_name,
                        "issue": "potential_false_positives",
                        "pass_rate": pass_rate,
                        "corrections": corrections_count,
                        "suggestion": "Gate passes almost everything but the "
                        "operator has corrected outputs. Consider tightening "
                        "thresholds.",
                    }
                )
            elif pass_rate < 0.20:
                gaps.append(
                    {
                        "gate": gate_name,
                        "issue": "potential_false_negatives",
                        "pass_rate": pass_rate,
                        "suggestion": "Gate fails almost everything. Consider "
                        "loosening thresholds.",
                    }
                )

        return gaps
