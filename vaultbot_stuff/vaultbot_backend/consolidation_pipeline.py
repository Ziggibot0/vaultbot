"""
Semantic Consolidation Pipeline — the "hippocampal replay" that mines
episodic memory (chat logs) for patterns and writes semantic knowledge notes.

Phases:
  1-2. Scan + Extract (pattern_extractor.py — already exists)
  3.    Cluster (this module — deterministic, groups patterns by shared links)
  4.    Synthesize (this module — LLM-assisted, scaffolded by pre-extracted patterns)
  5.    Validate (this module — deterministic checks)
  6.    Store + Link (this module — write note to vault with frontmatter + wikilinks)

Design principle: Extract mechanically, synthesize with LLM.
The framework does the heavy lifting; the LLM only writes prose from
pre-extracted, pre-clustered patterns.

See [[Semantic-Consolidation-Architecture]] for the full design.
"""

import json
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

# --- Token-economy consolidation mode ---
# llm        = always LLM synthesis (old behavior)
# template   = always deterministic template (default) — zero LLM calls
_CONSOLIDATION_MODE = os.getenv("VAULTBOT_CONSOLIDATION_MODE", "template").lower()


class ConsolidationPipeline:
    """The full consolidation pipeline: extract → cluster → synthesize → validate → store.

    Phase 1-2 are delegated to PatternExtractor (already exists).
    Phases 3-6 are implemented here.
    """

    def __init__(self, vault_path: str = None, backend_path: str = None):
        self.vault_path = vault_path or os.getenv("VAULT_PATH", ".")
        self.backend_path = backend_path or os.path.dirname(os.path.abspath(__file__))
        self.chat_dir = os.path.join(self.vault_path, "vaultbot_stuff", "Memory", "Chat")
        self.semantic_dir = os.path.join(self.vault_path, "vaultbot_stuff", "Memory", "Build-Log")
        self.log_path = os.path.join(self.backend_path, "consolidation_log.json")
        self._pattern_extractor = None

    @property
    def pattern_extractor(self):
        if self._pattern_extractor is None:
            from pattern_extractor import PatternExtractor
            self._pattern_extractor = PatternExtractor(vault_path=self.vault_path)
        return self._pattern_extractor

    # ------------------------------------------------------------------
    # Phase 3: Cluster (Deterministic)
    # ------------------------------------------------------------------

    def cluster_patterns(self, patterns: dict) -> list[dict]:
        """Group extracted patterns into clusters by shared wikilinks and themes.

        Each cluster becomes a candidate for one semantic note.
        Clustering is deterministic: patterns that share wikilinks or
        belong to the same category are grouped together.

        Returns:
            List of cluster dicts, sorted by priority (highest first).
        """
        clusters: list[dict[str, Any]] = []

        # Cluster 1: Recurring topics that co-occur in the same sessions
        recurring = patterns.get("recurring_topics", [])
        topic_clusters = self._cluster_by_co_occurrence(recurring)
        for theme, topics in topic_clusters.items():
            evidence_sources: set[str] = set()
            evidence_count = 0
            for t in topics:
                evidence_sources.update(t.get("sessions", []))
                evidence_count += t.get("total_mentions", 0)
            clusters.append({
                "theme": theme,
                "kind": "recurring_topic",
                "patterns": topics,
                "evidence_count": evidence_count,
                "evidence_sources": sorted(evidence_sources)[:10],
                "priority": min(100, evidence_count * 5),
            })

        # Cluster 2: Sentiment / correction patterns
        sentiment = patterns.get("sentiment", {})
        if sentiment.get("negative_rate", 0) > 0.15:
            neg_exchanges = sentiment.get("negative_exchanges", [])
            clusters.append({
                "theme": "operator-correction-patterns",
                "kind": "correction_pattern",
                "patterns": neg_exchanges[:10],
                "evidence_count": len(neg_exchanges),
                "evidence_sources": [e.get("file", "") for e in neg_exchanges[:10]],
                "priority": min(80, len(neg_exchanges) * 3),
                "negative_rate": sentiment.get("negative_rate", 0),
            })

        # Cluster 3: Over-reporting
        over_reporting = patterns.get("over_reporting", {})
        if over_reporting.get("count", 0) >= 3:
            clusters.append({
                "theme": "communication-brevity",
                "kind": "over_reporting",
                "patterns": over_reporting.get("exchanges", [])[:5],
                "evidence_count": over_reporting.get("count", 0),
                "evidence_sources": [
                    e.get("file", "") for e in over_reporting.get("exchanges", [])[:5]
                ],
                "priority": 40,
            })

        # Cluster 4: Tool workflow patterns
        tool_patterns = patterns.get("tool_patterns", {})
        top_workflows = tool_patterns.get("top_workflows", [])
        if top_workflows:
            significant = [w for w in top_workflows if w.get("count", 0) >= 10]
            if significant:
                clusters.append({
                    "theme": "tool-workflow-patterns",
                    "kind": "workflow_pattern",
                    "patterns": significant,
                    "evidence_count": sum(w.get("count", 0) for w in significant),
                    "evidence_sources": [],
                    "priority": 30,
                })

        clusters.sort(key=lambda c: -c.get("priority", 0))
        return clusters

    def _cluster_by_co_occurrence(
        self, recurring_topics: list[dict]
    ) -> dict[str, list[dict]]:
        """Group recurring topics that appear in the same sessions.

        Topics that co-occur in 2+ sessions likely belong to the same
        semantic cluster and should be consolidated together.
        """
        if not recurring_topics:
            return {}

        # Build session -> topics map
        session_topics: dict[str, list[str]] = defaultdict(list)
        for topic in recurring_topics:
            for session_file in topic.get("sessions", []):
                session_topics[session_file].append(topic["topic"])

        # Build topic -> co-occurring topics map
        topic_co_occurrence: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for _session, topics in session_topics.items():
            for i, t1 in enumerate(topics):
                for t2 in topics[i + 1:]:
                    topic_co_occurrence[t1][t2] += 1
                    topic_co_occurrence[t2][t1] += 1

        # Assign topics to clusters via strongest co-occurrence
        topic_to_cluster: dict[str, str] = {}
        cluster_id = 0
        for topic in recurring_topics:
            name = topic["topic"]
            if name in topic_to_cluster:
                continue
            co_topics = topic_co_occurrence.get(name, {})
            assigned = False
            for co_topic, count in sorted(co_topics.items(), key=lambda x: -x[1]):
                if count >= 2 and co_topic in topic_to_cluster:
                    topic_to_cluster[name] = topic_to_cluster[co_topic]
                    assigned = True
                    break
            if not assigned:
                topic_to_cluster[name] = f"cluster_{cluster_id}"
                cluster_id += 1

        # Group and label clusters
        clusters: dict[str, list[dict]] = defaultdict(list)
        topic_map = {t["topic"]: t for t in recurring_topics}
        for name, cid in topic_to_cluster.items():
            if name in topic_map:
                clusters[cid].append(topic_map[name])

        labeled: dict[str, list[dict]] = {}
        for _cid, topics in clusters.items():
            if len(topics) < 2:
                continue  # Single-topic clusters aren't worth consolidating
            topics.sort(key=lambda t: -t.get("session_count", 0))
            label = topics[0]["topic"]
            labeled[label] = topics

        return labeled

    # ------------------------------------------------------------------
    # Phase 4: Synthesize (LLM-Assisted or Template)
    # ------------------------------------------------------------------

    _TEMPLATE_IMPLICATIONS = {
        "recurring_topic": (
            "This topic has appeared across multiple sessions, suggesting it "
            "is a recurring focus area for the operator. Consider whether "
            "dedicated notes or a Map of Content would help consolidate the "
            "related knowledge."
        ),
        "correction_pattern": (
            "The operator frequently corrects responses of this type. This "
            "indicates a systematic misunderstanding or a style preference "
            "that should be adjusted in future interactions."
        ),
        "over_reporting": (
            "The operator prefers more concise communication. Future "
            "responses of this type should be trimmed to the essential "
            "information only."
        ),
        "workflow_pattern": (
            "This tool sequence is used frequently and could be a candidate "
            "for a custom procedure or composite tool."
        ),
    }

    def build_synthesis_template(self, cluster: dict) -> str:
        """Build a semantic note deterministically from pre-extracted patterns.

        Zero LLM calls. The patterns are already the analysis; this method
        just wraps them in note structure. The result has:
          - A # heading (theme)
          - A body paragraph (template per pattern kind)
          - Per-pattern bullets (deterministic extraction of description + evidence)
          - A ## What This Means section (templated per kind)
          - A ## Evidence section (wikilinks to evidence sources)
        """
        theme = cluster.get("theme", "unknown")
        kind = cluster.get("kind", "general")
        evidence_count = cluster.get("evidence_count", 0)
        evidence_sources = cluster.get("evidence_sources", [])
        patterns = cluster.get("patterns", [])

        lines: list[str] = []
        lines.append(f"# {theme.replace('-', ' ').title()}")
        lines.append("")

        # Body: describe the pattern type.
        lines.append(
            f"This note consolidates {evidence_count} observations of a "
            f"**{kind.replace('_', ' ')}** pattern across chat sessions."
        )
        lines.append("")

        # Per-pattern bullets.
        lines.append("## Observed Patterns")
        for p in patterns[:10]:
            desc = ""
            if isinstance(p, dict):
                desc = (p.get("topic") or p.get("description")
                        or p.get("name") or json.dumps(p, default=str)[:200])
                mentions = p.get("total_mentions") or p.get("count") or ""
                if mentions:
                    desc += f" ({mentions} mentions)"
            elif isinstance(p, str):
                desc = p[:200]
            if desc:
                lines.append(f"- {desc}")
        lines.append("")

        # What This Means (template per kind).
        lines.append("## What This Means")
        implication = self._TEMPLATE_IMPLICATIONS.get(
            kind,
            "This pattern recurs across sessions and may warrant dedicated "
            "knowledge structure (a note or a Map of Content)."
        )
        lines.append(implication)
        lines.append("")

        # Evidence section (wikilinks).
        lines.append("## Evidence")
        if evidence_sources:
            for src in evidence_sources[:10]:
                if src:
                    lines.append(f"- [[{src}]]")
        else:
            lines.append("- (no specific evidence sources recorded)")
        lines.append("")

        # Limitation note if evidence is thin.
        if evidence_count < 3:
            lines.append(
                f"> [!warning] Tentative pattern — only {evidence_count} "
                f"instance(s) observed. Confirm with more sessions."
            )
            lines.append("")

        return "\n".join(lines)

    def build_synthesis_prompt(self, cluster: dict) -> str:
        """Build the LLM prompt for synthesizing a cluster into a semantic note.

        The LLM receives pre-extracted patterns + evidence + an instruction.
        It writes prose. That's it. The framework did the analysis.
        """
        patterns_text = json.dumps(cluster["patterns"], indent=2, default=str)
        evidence_list = "\n".join(
            f"- [[{src}]]" for src in cluster.get("evidence_sources", [])
        )
        kind = cluster["kind"]
        theme = cluster["theme"]

        return (
            f"Write a semantic knowledge note that abstracts these patterns "
            f"into reusable insights.\n\n"
            f"PATTERN TYPE: {kind}\n"
            f"THEME: {theme}\n"
            f"EVIDENCE COUNT: {cluster.get('evidence_count', 0)}\n\n"
            f"EXTRACTED PATTERNS (deterministic findings — do NOT re-analyze, "
            f"just synthesize):\n{patterns_text}\n\n"
            f"EVIDENCE SOURCES (chat logs where these patterns were observed):\n"
            f"{evidence_list}\n\n"
            f"INSTRUCTIONS:\n"
            f"1. Write a self-contained argument: claim + reasoning + connections.\n"
            f"2. Use wikilinks [[like-this]] to connect to related notes.\n"
            f'3. Include a "## What This Means" section with the practical '
            f"implication.\n"
            f'4. Include a "## Evidence" section listing supporting chat logs.\n'
            f"5. Do NOT invent facts not present in the extracted patterns.\n"
            f"6. If evidence is thin (< 3 instances), note as a limitation.\n\n"
            f"Write the note body only (no frontmatter). Start with a # heading."
        )

    # ------------------------------------------------------------------
    # Phase 5: Validate (Deterministic)
    # ------------------------------------------------------------------

    def validate_note(self, note_content: str, cluster: dict) -> dict:
        """Run deterministic validation checks on a synthesized note.

        Returns:
            {'valid': bool, 'issues': list[str], 'warnings': list[str]}
        """
        issues: list[str] = []
        warnings: list[str] = []

        # Check 1: Has a heading
        if not re.search(r"^#\s+", note_content, re.MULTILINE):
            issues.append("No top-level heading found")

        # Check 2: Has wikilinks
        wikilinks = re.findall(r"\[\[([^\]]+)\]\]", note_content)
        if len(wikilinks) < 2:
            warnings.append(
                f"Only {len(wikilinks)} wikilinks — should have at least 2"
            )

        # Check 3: Has reasoning language
        reasoning_markers = [
            "because", "therefore", "which means", "this suggests",
            "however", "contradicts", "implies", "as a result",
        ]
        has_reasoning = any(
            marker in note_content.lower() for marker in reasoning_markers
        )
        if not has_reasoning:
            warnings.append(
                "No reasoning language found — note should explain WHY, "
                "not just state facts"
            )

        # Check 4: Evidence count is sufficient
        evidence_count = cluster.get("evidence_count", 0)
        if evidence_count < 3:
            warnings.append(
                f"Evidence count is {evidence_count} — pattern is tentative, "
                f"mark as status: tentative"
            )

        # Check 5: Not too short
        if len(note_content) < 200:
            issues.append("Note is too short (< 200 chars)")

        # Check 6: Not too long (over-reporting guard)
        if len(note_content) > 5000:
            warnings.append("Note is long (> 5000 chars) — consider trimming")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Phase 6: Store + Link
    # ------------------------------------------------------------------

    def build_note(self, cluster: dict, synthesized_content: str) -> str:
        """Build the full note with frontmatter, ready to write to the vault.

        Includes the semantic note schema from the architecture:
        type, status, evidence_count, evidence_sources, scope, falsifiable_if.
        """
        evidence_count = cluster.get("evidence_count", 0)
        status = "verified" if evidence_count >= 3 else "tentative"
        evidence_sources = cluster.get("evidence_sources", [])[:10]

        evidence_links = "\n".join(
            f'  - "[[{src}]]"' for src in evidence_sources
        )

        frontmatter = (
            f"---\n"
            f"type: semantic\n"
            f"status: {status}\n"
            f"created: {datetime.now(UTC).strftime('%Y-%m-%d')}\n"
            f"last_reviewed: {datetime.now(UTC).strftime('%Y-%m-%d')}\n"
            f"review_interval_days: 60\n"
            f"evidence_count: {evidence_count}\n"
            f"evidence_sources:\n{evidence_links}\n"
            f"scope:\n"
            f"  - sessions\n"
            f"  - {cluster.get('kind', 'general')}\n"
            f'falsifiable_if: "a future session contradicts this pattern '
            f'with new evidence"\n'
            f"tags: [semantic, pattern, consolidation, "
            f"{cluster.get('kind', 'general')}]\n"
            f"---"
        )

        full_note = f"{frontmatter}\n\n{synthesized_content}"

        # Pass through inject_schema to fill any missing universal fields
        try:
            from note_schema import inject_schema
            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", cluster.get("theme", "unknown"))[:60]
            full_note = inject_schema(
                full_note,
                f"vaultbot_stuff/Memory/Build-Log/Semantic-{safe_name}.md",
                force_type="semantic",
            )
        except ImportError:
            pass

        return full_note

    def store_note(self, note_content: str, theme: str) -> str:
        """Write the semantic note to the vault. Returns the file path."""
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", theme)[:60]
        filename = f"Semantic-{safe_name}.md"
        filepath = os.path.join(self.semantic_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(note_content)

        return filepath

    # ------------------------------------------------------------------
    # Full Pipeline
    # ------------------------------------------------------------------

    def run(self, since_timestamp: str = None) -> dict:
        """Run the full consolidation pipeline (Phases 1-4).

        Phases 1-2: Extract patterns (PatternExtractor)
        Phase 3:    Cluster patterns (this module)
        Phase 4:    Build synthesis prompts (LLM call happens outside)

        Returns:
            {
                'patterns': dict,
                'clusters': list[dict],
                'synthesis_prompts': list[dict],  # cluster + prompt pairs
                'timestamp': str,
            }
        """
        # Phases 1-2: Extract
        patterns = self.pattern_extractor.extract_all()

        # Phase 3: Cluster
        clusters = self.cluster_patterns(patterns)

        # Phase 4: Build synthesis prompts (LLM call happens outside)
        synthesis_prompts = []
        for cluster in clusters[:5]:  # Top 5 clusters per run
            prompt = self.build_synthesis_prompt(cluster)
            synthesis_prompts.append({
                "cluster": cluster,
                "prompt": prompt,
            })

        # Log this consolidation run
        self._log_consolidation(patterns, clusters)

        return {
            "patterns": patterns,
            "clusters": clusters,
            "synthesis_prompts": synthesis_prompts,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def finalize_note(self, cluster: dict, synthesized_content: str) -> dict:
        """Validate and store a synthesized note (Phases 5-6).

        Called after the LLM has produced content from the synthesis prompt.
        """
        # Phase 5: Validate
        validation = self.validate_note(synthesized_content, cluster)

        if not validation["valid"]:
            return {
                "ok": False,
                "error": "Validation failed",
                "issues": validation["issues"],
                "warnings": validation["warnings"],
            }

        # Phase 6: Build + Store
        full_note = self.build_note(cluster, synthesized_content)
        filepath = self.store_note(full_note, cluster["theme"])

        return {
            "ok": True,
            "note_path": filepath,
            "warnings": validation["warnings"],
        }

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_consolidation(self, patterns: dict, clusters: list[dict]):
        """Log this consolidation run for tracking."""
        try:
            log: dict[str, Any] = {
                "consolidations": [],
                "last_consolidation": None,
            }
            if os.path.exists(self.log_path):
                with open(self.log_path, encoding="utf-8") as f:
                    log = json.load(f)

            log["consolidations"].append({
                "timestamp": datetime.now(UTC).isoformat(),
                "total_sessions": patterns.get("total_sessions", 0),
                "total_exchanges": patterns.get("total_exchanges", 0),
                "clusters_found": len(clusters),
                "top_clusters": [c["theme"] for c in clusters[:5]],
            })
            log["last_consolidation"] = datetime.now(UTC).isoformat()

            # Keep bounded
            if len(log["consolidations"]) > 50:
                log["consolidations"] = log["consolidations"][-50:]

            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(log, f, indent=2, ensure_ascii=False)
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass  # Logging is best-effort
