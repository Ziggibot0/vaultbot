"""
Calibration Tracker — uses the operator's corrections as ground truth to calibrate
automated quality gates (vault_lint, procedure_tracker, etc.).

Pure deterministic. No LLM calls. Structured logging + simple statistics.

See [[Calibration-via-Operator-Feedback]] for the architecture rationale.
"""

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any


def _clamp01(value: float) -> float:
    """Clamp a numeric value into the inclusive 0..1 confidence range."""
    return max(0.0, min(1.0, float(value)))


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
                json.dump(
                    {
                        "corrections": [],
                        "gate_decisions": [],
                        "answer_confidence": [],
                    },
                    f,
                    indent=2,
                )

    def _with_defaults(self, data: dict) -> dict:
        data.setdefault("corrections", [])
        data.setdefault("gate_decisions", [])
        data.setdefault("answer_confidence", [])
        return data

    def _load(self) -> dict:
        with open(self.log_path, encoding="utf-8") as f:
            return self._with_defaults(json.load(f))

    def _save(self, data: dict):
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _answer_key(self, answer: str) -> str:
        normalized = re.sub(r"\s+", " ", (answer or "").strip())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _confidence_band(self, confidence: float) -> str:
        confidence = _clamp01(confidence)
        if confidence < 0.4:
            return "low"
        if confidence < 0.75:
            return "moderate"
        return "high"

    def _grounding_confidence(self, grounding_score: dict[str, Any] | None) -> float:
        """Convert grounding metrics into a raw answer-confidence estimate."""
        score = grounding_score or {}
        total = int(score.get("total_wikilinks", 0) or 0)
        sentences = int(score.get("sentences", 0) or 0)
        failed = bool(score.get("failed", False))
        grounding = _clamp01(score.get("grounding_score", 0.0) or 0.0)
        ungrounded_ratio = _clamp01(score.get("ungrounded_ratio", 0.0) or 0.0)

        if total == 0:
            if sentences <= 1:
                return 0.25
            return 0.05 if failed else 0.15

        citation_component = grounding
        sentence_component = 1.0 - ungrounded_ratio
        raw = (0.65 * citation_component) + (0.35 * sentence_component)
        if failed:
            raw *= 0.7
        return round(_clamp01(raw), 4)

    def _parse_verification_summary(
        self, verification_summary: str | dict[str, Any] | None
    ) -> dict[str, Any]:
        if isinstance(verification_summary, dict):
            return verification_summary
        if (
            not isinstance(verification_summary, str)
            or not verification_summary.strip()
        ):
            return {}
        try:
            parsed = json.loads(verification_summary)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _verification_confidence(
        self, verification_summary: dict[str, Any] | None
    ) -> float:
        """Roll per-claim entailment verdicts up into an answer-confidence score."""
        summary = verification_summary or {}
        supported = max(0, int(summary.get("supported", 0) or 0))
        unsupported = max(0, int(summary.get("unsupported", 0) or 0))
        contradicted = max(0, int(summary.get("contradicted", 0) or 0))
        total = max(
            0,
            int(
                summary.get(
                    "total",
                    supported + unsupported + contradicted,
                )
                or 0
            ),
        )
        if total <= 0:
            return 0.0
        weighted_negative = unsupported + (2 * contradicted)
        denom = supported + weighted_negative
        if denom <= 0:
            return 0.0
        return round(_clamp01(supported / denom), 4)

    def calibrate_confidence(self, raw_confidence: float) -> dict[str, Any]:
        """Calibrate a raw confidence estimate against historical outcomes."""
        raw = round(_clamp01(raw_confidence), 4)
        data = self._load()
        labeled = [
            entry
            for entry in data.get("answer_confidence", [])
            if isinstance(entry.get("observed_quality"), (int, float))
        ]
        if not labeled:
            return {
                "raw_confidence": raw,
                "calibrated_confidence": raw,
                "bucket": int(min(raw, 0.9999) * 10),
                "sample_size": 0,
                "calibration_scope": "raw",
            }

        bucket = int(min(raw, 0.9999) * 10)
        same_bucket = [
            entry
            for entry in labeled
            if int(min(_clamp01(entry.get("raw_confidence", 0.0)), 0.9999) * 10)
            == bucket
        ]
        nearby = [
            entry
            for entry in labeled
            if abs(
                int(min(_clamp01(entry.get("raw_confidence", 0.0)), 0.9999) * 10)
                - bucket
            )
            <= 1
        ]
        reference = same_bucket or nearby or labeled
        scope = "bucket" if same_bucket else "nearby" if nearby else "global"
        empirical = sum(float(e["observed_quality"]) for e in reference) / len(
            reference
        )
        max_weight = 0.75 if scope == "bucket" else 0.55 if scope == "nearby" else 0.35
        weight = min(max_weight, len(reference) / 8)
        calibrated = ((1.0 - weight) * raw) + (weight * empirical)
        return {
            "raw_confidence": raw,
            "calibrated_confidence": round(_clamp01(calibrated), 4),
            "bucket": bucket,
            "sample_size": len(reference),
            "calibration_scope": scope,
        }

    def estimate_answer_confidence(
        self,
        grounding_score: dict[str, Any] | None = None,
        verification_summary: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Estimate calibrated answer confidence from grounding or verification."""
        if verification_summary is not None:
            summary = self._parse_verification_summary(verification_summary)
            if not summary:
                return {}
            payload = self.calibrate_confidence(self._verification_confidence(summary))
            payload.update(
                {
                    "stage": "verified",
                    "band": self._confidence_band(payload["calibrated_confidence"]),
                    "verification_summary": summary,
                    "total_claims": int(summary.get("total", 0) or 0),
                    "supported_claims": int(summary.get("supported", 0) or 0),
                    "unsupported_claims": int(summary.get("unsupported", 0) or 0),
                    "contradicted_claims": int(summary.get("contradicted", 0) or 0),
                }
            )
            return payload

        if grounding_score is None:
            return {}
        payload = self.calibrate_confidence(self._grounding_confidence(grounding_score))
        payload.update(
            {
                "stage": "grounding",
                "band": self._confidence_band(payload["calibrated_confidence"]),
                "total_citations": int(grounding_score.get("total_wikilinks", 0) or 0),
                "allowed_cited": int(grounding_score.get("allowed_cited", 0) or 0),
                "ungrounded_ratio": float(
                    grounding_score.get("ungrounded_ratio", 0.0) or 0.0
                ),
                "grounding_score": float(
                    grounding_score.get("grounding_score", 0.0) or 0.0
                ),
                "failed": bool(grounding_score.get("failed", False)),
            }
        )
        return payload

    def log_answer_confidence(self, answer: str, confidence: dict[str, Any]) -> dict:
        """Persist a confidence estimate so future turns can calibrate against it."""
        if not answer or not confidence:
            return {}
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "answer_key": self._answer_key(answer),
            "answer_excerpt": (answer or "")[:240],
            "stage": confidence.get("stage", "unknown"),
            "band": confidence.get(
                "band",
                self._confidence_band(confidence.get("calibrated_confidence", 0.0)),
            ),
            "raw_confidence": round(
                _clamp01(confidence.get("raw_confidence", 0.0) or 0.0), 4
            ),
            "calibrated_confidence": round(
                _clamp01(confidence.get("calibrated_confidence", 0.0) or 0.0), 4
            ),
            "bucket": int(confidence.get("bucket", 0) or 0),
            "sample_size": int(confidence.get("sample_size", 0) or 0),
            "calibration_scope": confidence.get("calibration_scope", "raw"),
            "corrected": False,
        }
        if confidence.get("stage") == "verified":
            entry["observed_quality"] = entry["raw_confidence"]
            entry["outcome_source"] = "verification"
            entry["verification_summary"] = confidence.get("verification_summary", {})
        data = self._load()
        assessments = data.get("answer_confidence", [])
        for idx in range(len(assessments) - 1, -1, -1):
            existing = assessments[idx]
            if (
                existing.get("answer_key") == entry["answer_key"]
                and existing.get("stage") == entry["stage"]
            ):
                assessments[idx] = entry
                break
        else:
            assessments.append(entry)
        data["answer_confidence"] = assessments[-400:]
        self._save(data)
        return entry

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
        answer_key = self._answer_key(prev_answer) if prev_answer else ""
        matched_assessment = False
        if answer_key:
            for assessment in reversed(data.get("answer_confidence", [])):
                if assessment.get("answer_key") != answer_key:
                    continue
                assessment["corrected"] = True
                assessment["observed_quality"] = 0.0
                assessment["outcome_source"] = "operator_correction"
                assessment["correction_message"] = user_message[:500]
                assessment["failure_type"] = failure_type
                assessment["corrected_at"] = entry["timestamp"]
                matched_assessment = True
                break
        entry["matched_answer_confidence"] = matched_assessment
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

        assessments = data.get("answer_confidence", [])
        labeled = [
            a
            for a in assessments
            if isinstance(a.get("observed_quality"), (int, float))
        ]
        if labeled:
            report["_answer_confidence"] = {
                "total_assessments": len(assessments),
                "labeled_assessments": len(labeled),
                "average_raw_confidence": round(
                    sum(float(a.get("raw_confidence", 0.0)) for a in labeled)
                    / len(labeled),
                    4,
                ),
                "average_observed_quality": round(
                    sum(float(a.get("observed_quality", 0.0)) for a in labeled)
                    / len(labeled),
                    4,
                ),
                "corrected_answers": sum(
                    1 for a in labeled if bool(a.get("corrected", False))
                ),
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
