"""
Claim Verifier - verifies that synthesized claims in vault notes match their
cited sources. Post-generation verification layer for VaultBot research pipeline.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime

# --- Token-economy claim-verification mode ---
# det     = deterministic only (default) — zero LLM calls
# llm     = LLM only (fails if no LLM)
# hybrid  = deterministic first, LLM only for borderline (match ratio 0.1–0.3)
_CLAIM_VERIFY_MODE = os.getenv("VAULTBOT_CLAIM_VERIFY_MODE", "det").lower()

# Borderline match-ratio zone for hybrid entailment (between these → use LLM).
_BORDERLINE_LOW = 0.1
_BORDERLINE_HIGH = 0.3


class ClaimVerifier:
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    UNSOURCED = "unsourced"
    SOURCE_NOT_FOUND = "source_not_found"
    ERROR = "error"

    def __init__(self, llm_client=None, log_path=None, vault_root=None, max_source_chars=8000):
        self.llm_client = llm_client
        self.log_path = log_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "claim_verification_log.json")
        self.vault_root = vault_root or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        self.max_source_chars = max_source_chars
        self._ensure_log()

    def _ensure_log(self):
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump({"verification_logs": []}, f)

    def _load_log(self):
        try:
            with open(self.log_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"verification_logs": []}

    def _save_log(self, data):
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _parse_sources_section(self, note_content):
        sources = {}
        sources_match = re.search(r'##\s*Sources\s*\n(.*?)(?:\n##\s|\n<!--\s*research:|\Z)', note_content, re.DOTALL)
        if not sources_match:
            return sources
        sources_text = sources_match.group(1)
        for line in sources_text.split('\n'):
            line = line.strip()
            if not line.startswith('-'):
                continue
            link_match = re.match(r'-\s*\[([^\]]+)\]\(([^)]+)\)', line)
            if not link_match:
                continue
            title = link_match.group(1).strip()
            url = link_match.group(2).strip()
            archived_match = re.search(r'\[\[learningMaterial/web/([^|]+?)(?:\|archived)?\]\]', line)
            archived_filename = archived_match.group(1).strip() if archived_match else None
            sources[title.lower()] = {"title": title, "url": url, "archived_filename": archived_filename}
        return sources

    def _extract_source_citation(self, claim_text):
        match = re.search(r'\[sources?:\s*([^\]]+)\]', claim_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _load_source_text(self, source_info):
        if source_info.get("archived_filename"):
            try:
                from web_source_store import read_source_text
                text = read_source_text(source_info["archived_filename"])
                if text and len(text) > 50:
                    return text
            except Exception:
                pass
        if source_info.get("url"):
            try:
                from web_source_store import find_source, read_source_text
                entry = find_source(source_info["url"])
                if entry:
                    text = read_source_text(entry["file"])
                    if text and len(text) > 50:
                        return text
            except Exception:
                pass
        return None

    def _llm_available(self):
        if not self.llm_client:
            return False
        try:
            return self.llm_client.is_running()
        except Exception:
            return False

    def _llm_extract_claims(self, synthesis_text):
        prompt = ("You are a claim extraction system. Extract all atomic factual claims "
                  "from the following research text. Each claim should be a single "
                  "verifiable sentence. Preserve any [sources: ...] citation.\n\n"
                  "Return a JSON array of objects with 'claim' and 'source' fields.\n\n"
                  "Text:\n" + synthesis_text[:6000] + "\n\nReturn ONLY the JSON array.")
        try:
            response = self.llm_client.chat([{"role": "user", "content": prompt}], temperature=0.1, stream=False)
            raw = ""
            if isinstance(response, dict):
                raw = response.get("message", {}).get("content", "") or response.get("response", "")
            elif isinstance(response, str):
                raw = response
            raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
            raw = re.sub(r'\s*```$', '', raw.strip())
            claims_data = json.loads(raw)
            if isinstance(claims_data, list):
                return [{"claim": c.get("claim", ""), "source": c.get("source")} for c in claims_data if c.get("claim")]
        except Exception:
            pass
        return []

    def _deterministic_extract_claims(self, synthesis_text):
        claims = []
        for line in synthesis_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('```'):
                continue
            text = re.sub(r'^[-*]\s*', '', line)
            if len(text) < 10:
                continue
            source = self._extract_source_citation(text)
            claims.append({"claim": text, "source": source})
        return claims

    def extract_claims(self, note_content):
        synthesis_match = re.search(r'##\s*(?:Key Findings|Synthesis|Summary)\s*\n(.*?)(?:\n##\s|\Z)', note_content, re.DOTALL)
        synthesis_text = synthesis_match.group(1) if synthesis_match else note_content

        mode = _CLAIM_VERIFY_MODE
        if mode == "llm":
            if self._llm_available():
                claims = self._llm_extract_claims(synthesis_text)
                if claims:
                    return claims
            return self._deterministic_extract_claims(synthesis_text)

        if mode == "hybrid":
            # Deterministic first; only escalate to LLM if it found too few
            # claims (likely missed complex multi-sentence claims).
            claims = self._deterministic_extract_claims(synthesis_text)
            if len(claims) < 3 and self._llm_available():
                llm_claims = self._llm_extract_claims(synthesis_text)
                if len(llm_claims) > len(claims):
                    return llm_claims
            return claims

        # det (default): deterministic only.
        return self._deterministic_extract_claims(synthesis_text)

    def _llm_check_entailment(self, claim, source_text):
        source_excerpt = source_text[:self.max_source_chars]
        prompt = ("You are a fact-checking system. Given a source text and a claim, "
                  "determine whether the source supports the claim.\n\n"
                  "Verdict: 'supported', 'unsupported', or 'contradicted'.\n\n"
                  'Return JSON: {"verdict": "...", "reasoning": "..."}\n\n'
                  "Source text:\n" + source_excerpt + "\n\nClaim:\n" + claim + "\n\nReturn ONLY the JSON.")
        try:
            response = self.llm_client.chat([{"role": "user", "content": prompt}], temperature=0.1, stream=False)
            raw = ""
            if isinstance(response, dict):
                raw = response.get("message", {}).get("content", "") or response.get("response", "")
            elif isinstance(response, str):
                raw = response
            raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
            raw = re.sub(r'\s*```$', '', raw.strip())
            result = json.loads(raw)
            verdict = result.get("verdict", self.UNSUPPORTED).lower()
            if verdict not in (self.SUPPORTED, self.UNSUPPORTED, self.CONTRADICTED):
                verdict = self.UNSUPPORTED
            return {"verdict": verdict, "reasoning": result.get("reasoning", "")}
        except Exception as e:
            return {"verdict": self.ERROR, "reasoning": f"LLM error: {e}"}

    def _deterministic_check_entailment(self, claim, source_text):
        def normalize(text):
            text = text.lower()
            text = re.sub(r'[^\w\s]', ' ', text)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()
        claim_norm = normalize(claim)
        source_norm = normalize(source_text)
        if not claim_norm or not source_norm:
            return {"verdict": self.UNSUPPORTED, "reasoning": "Empty text"}
        claim_words = claim_norm.split()
        key_phrases = []
        for i in range(len(claim_words) - 2):
            key_phrases.append(' '.join(claim_words[i:i+3]))
        if not key_phrases:
            claim_words_set = set(claim_words)
            source_words_set = set(source_norm.split())
            overlap = len(claim_words_set & source_words_set)
            if overlap >= len(claim_words_set) * 0.5:
                return {"verdict": self.SUPPORTED, "reasoning": "Word overlap >= 50%"}
            return {"verdict": self.UNSUPPORTED, "reasoning": "Insufficient word overlap"}
        matches = sum(1 for p in key_phrases if p in source_norm)
        match_ratio = matches / len(key_phrases) if key_phrases else 0
        if match_ratio >= 0.3:
            return {"verdict": self.SUPPORTED, "reasoning": f"{match_ratio:.0%} of key phrases found in source"}
        elif match_ratio >= 0.1:
            return {"verdict": self.UNSUPPORTED, "reasoning": f"Only {match_ratio:.0%} of key phrases found"}
        else:
            return {"verdict": self.UNSUPPORTED, "reasoning": "No key phrase overlap with source"}

    def check_entailment(self, claim, source_text):
        mode = _CLAIM_VERIFY_MODE
        if mode == "llm":
            if self._llm_available():
                result = self._llm_check_entailment(claim, source_text)
                if result["verdict"] != self.ERROR:
                    return result
            return self._deterministic_check_entailment(claim, source_text)

        if mode == "hybrid":
            # Deterministic first; escalate to LLM only for borderline ratios.
            det_result = self._deterministic_check_entailment(claim, source_text)
            # Extract the match ratio from the reasoning if present.
            ratio_match = re.search(r'(\d+)%', det_result.get("reasoning", ""))
            if ratio_match:
                ratio = int(ratio_match.group(1)) / 100.0
                if _BORDERLINE_LOW < ratio < _BORDERLINE_HIGH and self._llm_available():
                    llm_result = self._llm_check_entailment(claim, source_text)
                    if llm_result["verdict"] != self.ERROR:
                        return llm_result
            return det_result

        # det (default): deterministic only.
        return self._deterministic_check_entailment(claim, source_text)

    def verify_note(self, note_path):
        try:
            with open(note_path, encoding="utf-8") as f:
                note_content = f.read()
        except Exception as e:
            return {"error": f"Could not read note: {e}"}
        sources_index = self._parse_sources_section(note_content)
        claims = self.extract_claims(note_content)
        if not claims:
            return {"note_path": note_path, "total_claims": 0, "verified": 0, "unsupported": 0,
                    "contradicted": 0, "unsourced": 0, "source_not_found": 0, "claims": [],
                    "message": "No claims extracted"}
        verified_claims = []
        stats = defaultdict(int)
        stats["total_claims"] = len(claims)
        for claim_data in claims:
            claim_text = claim_data["claim"]
            source_title = claim_data.get("source") or self._extract_source_citation(claim_text)
            if not source_title:
                result = {"claim": claim_text, "source": None, "verdict": self.UNSOURCED, "reasoning": "No source citation found"}
                stats[self.UNSOURCED] += 1
                verified_claims.append(result)
                continue
            source_info = sources_index.get(source_title.lower())
            if not source_info:
                for key, info in sources_index.items():
                    if source_title.lower() in key or key in source_title.lower():
                        source_info = info
                        break
            if not source_info:
                result = {"claim": claim_text, "source": source_title, "verdict": self.SOURCE_NOT_FOUND, "reasoning": "Source not found in note sources"}
                stats[self.SOURCE_NOT_FOUND] += 1
                verified_claims.append(result)
                continue
            source_text = self._load_source_text(source_info)
            if not source_text:
                result = {"claim": claim_text, "source": source_title, "verdict": self.SOURCE_NOT_FOUND, "reasoning": "Could not load archived source file"}
                stats[self.SOURCE_NOT_FOUND] += 1
                verified_claims.append(result)
                continue
            clean_claim = re.sub(r'\s*\[sources?:\s*[^\]]+\]', '', claim_text, flags=re.IGNORECASE)
            entailment = self.check_entailment(clean_claim, source_text)
            result = {"claim": claim_text, "source": source_title, "verdict": entailment["verdict"], "reasoning": entailment["reasoning"]}
            stats[entailment["verdict"]] += 1
            verified_claims.append(result)
        report = {"note_path": note_path, "timestamp": datetime.utcnow().isoformat() + "Z",
                  "total_claims": stats["total_claims"], "verified": stats[self.SUPPORTED],
                  "unsupported": stats[self.UNSUPPORTED], "contradicted": stats[self.CONTRADICTED],
                  "unsourced": stats[self.UNSOURCED], "source_not_found": stats[self.SOURCE_NOT_FOUND],
                  "claims": verified_claims}
        self._update_frontmatter(note_path, report)
        self._log_verification(report)
        return report

    def _update_frontmatter(self, note_path, report):
        try:
            with open(note_path, encoding="utf-8") as f:
                content = f.read()
            verification_yaml = (f"verification:\n  total_claims: {report['total_claims']}\n"
                f"  verified: {report['verified']}\n  unsupported: {report['unsupported']}\n"
                f"  contradicted: {report['contradicted']}\n  unsourced: {report['unsourced']}\n"
                f"  source_not_found: {report['source_not_found']}\n"
                f"  last_verified: \"{report['timestamp']}\"")
            fm_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
            if fm_match:
                fm_content = fm_match.group(1)
                if re.search(r'^verification:', fm_content, re.MULTILINE):
                    new_fm = re.sub(r'verification:\n(?:  \w+:.*\n)*  last_verified:.*', verification_yaml, fm_content, flags=re.MULTILINE)
                else:
                    new_fm = fm_content.rstrip() + "\n" + verification_yaml
                new_content = f"---\n{new_fm}\n---\n" + content[fm_match.end():]
            else:
                new_content = f"---\n{verification_yaml}\n---\n" + content
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception:
            pass

    def _log_verification(self, report):
        data = self._load_log()
        compact = {"note_path": report["note_path"], "timestamp": report["timestamp"],
                   "total_claims": report["total_claims"], "verified": report["verified"],
                   "unsupported": report["unsupported"], "contradicted": report["contradicted"],
                   "unsourced": report["unsourced"], "source_not_found": report["source_not_found"],
                   "failed_claims": [{"claim": c["claim"][:200], "verdict": c["verdict"], "reasoning": c.get("reasoning", "")[:200]}
                                     for c in report.get("claims", []) if c["verdict"] != self.SUPPORTED]}
        data["verification_logs"].append(compact)
        if len(data["verification_logs"]) > 200:
            data["verification_logs"] = data["verification_logs"][-200:]
        self._save_log(data)

    def get_verification_summary(self):
        data = self._load_log()
        logs = data.get("verification_logs", [])
        if not logs:
            return {"total_notes_verified": 0}
        total_claims = sum(l.get("total_claims", 0) for l in logs)
        total_verified = sum(l.get("verified", 0) for l in logs)
        total_unsupported = sum(l.get("unsupported", 0) for l in logs)
        total_contradicted = sum(l.get("contradicted", 0) for l in logs)
        total_unsourced = sum(l.get("unsourced", 0) for l in logs)
        total_not_found = sum(l.get("source_not_found", 0) for l in logs)
        return {"total_notes_verified": len(logs), "total_claims": total_claims,
                "total_verified": total_verified, "total_unsupported": total_unsupported,
                "total_contradicted": total_contradicted, "total_unsourced": total_unsourced,
                "total_source_not_found": total_not_found,
                "verification_rate": total_verified / total_claims if total_claims else 0,
                "failure_rate": (total_unsupported + total_contradicted) / total_claims if total_claims else 0}

    def get_verification_gaps(self):
        data = self._load_log()
        logs = data.get("verification_logs", [])
        gaps = []
        for log in logs:
            total = log.get("total_claims", 0)
            if total == 0:
                continue
            verified = log.get("verified", 0)
            unsupported = log.get("unsupported", 0)
            contradicted = log.get("contradicted", 0)
            unsourced = log.get("unsourced", 0)
            if contradicted > 0:
                gaps.append({"note_path": log["note_path"], "issue": f"{contradicted} contradicted claim(s)", "severity": "high", "failed_claims": log.get("failed_claims", [])})
            if total > 0 and unsupported / total > 0.3:
                gaps.append({"note_path": log["note_path"], "issue": f"{unsupported}/{total} claims unsupported ({unsupported/total:.0%})", "severity": "medium", "failed_claims": log.get("failed_claims", [])})
            if unsourced > 0:
                gaps.append({"note_path": log["note_path"], "issue": f"{unsourced} unsourced claim(s)", "severity": "low", "failed_claims": log.get("failed_claims", [])})
        return gaps
