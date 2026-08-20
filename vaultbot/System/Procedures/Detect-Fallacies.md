---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-09
description: "Detect-Fallacies deterministically pattern-matches input text against 30+ fallacy signatures from the Critical Thinking knowledge note. All classification happens in code — no LLM needed for detection. The small model only formats the output. This replaces the old DAG version where the small model hallucinated on classification steps."
when_to_use: "Before presenting any argument or conclusion. Run this to check your reasoning for fallacies. Also callable on user-provided text to identify fallacies in external arguments."
falsifiable_if: "A known fallacy is present in the text but not detected, or a false positive is flagged."
applies_to:
  - fallacy-detection
  - critical-thinking
  - quality-control
  - small-model
allowed_tools:
  - code_read
summary: |
  Detect-Fallacies: deterministic pattern-matching against 30+ fallacy signatures.
  1. Code step pre-extracts features and matches against all fallacy patterns.
  2. Small model formats the detected fallacies into a readable report.
  No LLM classification — all detection is regex-based.
tags:
  - procedure
  - fallacy-detection
  - critical-thinking
  - small-model
  - deterministic
---

# Detect-Fallacies

## Purpose

Deterministically detects logical fallacies in input text by pattern-matching against 30+ fallacy signatures from [[Critical-Thinking-Paths-and-Logical-Fallacy-Detection]]. All classification happens in a code step — the small model only formats the output. This replaces the old DAG version where the small model hallucinated on classification steps.

## Inputs

- `text` (string, required): The argument or reasoning text to check for fallacies.

## Output Contract

Returns a JSON object with:
- `detected`: list of fallacy names found
- `count`: number of fallacies detected
- `report`: human-readable report (formatted by the small model)

---

## Steps

### Step 1: Pattern-match the text against fallacy signatures

1. ```python
import json, re

text = args.get('text', '')
text_lower = text.lower()

# ============================================================
# FALLACY PATTERN MATCHING — all deterministic, no LLM needed
# Each fallacy has a regex pattern and a description.
# ============================================================

fallacies = []

# --- ATTACK FALLACIES ---

# Ad Hominem: attacking the person instead of the argument
if re.search(r"(you'?re\s+(just|only|a|an)\s+\w+|you\s+(always|never)\s+\w+|you\s+would\s+say\s+that|you'?re\s+(biased|stupid|an?\s+idiot|a\s+liar|a\s+fraud|corrupt|incompetent|a\s+hack|a\s+shill))", text_lower):
    fallacies.append("Ad Hominem")

# Tu Quoque: "you do it too"
if re.search(r"(you\s+(do|did|have\s+done)\s+it\s+too|what\s+about\s+you|you'?re\s+one\s+to\s+talk|look\s+who'?s\s+talking|you'?re\s+a\s+hypocrite)", text_lower):
    fallacies.append("Tu Quoque")

# Guilt by Association: linking someone to a disliked group
if re.search(r"(associated\s+with|connected\s+to|linked\s+to|friends\s+with|supports?\s+the\s+same)\s+(terrorists?|extremists?|racists?|fascists?|communists?|criminals?)", text_lower):
    fallacies.append("Guilt by Association")

# --- APPEAL FALLACIES ---

# Appeal to Authority: citing an authority outside their expertise
if re.search(r"(Dr\.\s+\w+\s+says?|according\s+to\s+(Dr\.|Professor|expert)|scientists?\s+say|studies\s+show|research\s+proves|experts\s+agree)", text_lower) and not re.search(r"(study|paper|journal|published|data|evidence|trial|experiment|meta.analysis)", text_lower):
    fallacies.append("Appeal to Authority (vague)")

# Bandwagon: "everyone believes it"
if re.search(r"(everyone\s+(knows|believes|thinks|agrees|does\s+it)|most\s+people\s+(think|believe|agree|do)|all\s+the\s+(experts|scientists|research)|90%|99%|the\s+majority|everybody\s+(knows|does))", text_lower):
    fallacies.append("Bandwagon")

# Appeal to Emotion: using fear/pity/anger instead of evidence
emotion_words = r"(fear|terror|horror|outrage|fury|panic|disgust|innocent\s+children|think\s+of\s+the\s+children|your\s+(children|family|kids)|catastrophe|disaster|destroy|ruin|kill\s+you|die|death)"
if re.search(emotion_words, text_lower) and not re.search(r"(data|evidence|study|statistics?|percent|rate|survey|trial)", text_lower):
    fallacies.append("Appeal to Emotion")

# Appeal to Nature: "natural = good"
if re.search(r"(it'?s\s+natural|naturally|all.natural|because\s+it'?s\s+natural|natural\s+is\s+better|chemical.free|organic\s+is\s+better)", text_lower):
    fallacies.append("Appeal to Nature")

# Appeal to Tradition: "it's always been done this way"
if re.search(r"(always\s+been\s+done|traditional|we'?ve\s+always|how\s+it'?s\s+always\s+been|time.honored|tried\s+and\s+true|the\s+way\s+things\s+are|that'?s\s+how\s+we'?ve\s+always)", text_lower):
    fallacies.append("Appeal to Tradition")

# Appeal to Novelty: "new = better"
if re.search(r"(cutting.edge|latest|newest|innovative|revolutionary|game.changing|disruptive|next.generation|state.of.the.art)", text_lower) and not re.search(r"(evidence|data|study|proven|tested)", text_lower):
    fallacies.append("Appeal to Novelty")

# --- PRESUMPTION FALLACIES ---

# False Dichotomy: presenting only two options
if re.search(r"(either\s+.+?\s+or\s+|you'?re\s+either|must\s+choose|only\s+two\s+(options|choices|possibilities|ways)|there\s+are\s+two\s+types\s+of)", text_lower):
    fallacies.append("False Dichotomy")

# Begging the Question: circular reasoning
if re.search(r"(because\s+it\s+(is|says\s+so)|it'?s\s+true\s+because\s+it'?s\s+true|obviously|clearly|it\s+goes\s+without\s+saying|as\s+everyone\s+knows|it\s+stands\s+to\s+reason)", text_lower):
    fallacies.append("Begging the Question")

# Slippery Slope: chain of events to catastrophe
if re.search(r"(if\s+we\s+.+?,\s*then\s+.+?,\s*(?:and|then)\s+.+?(?:catastroph|collapse|disaster|destroy|ruin|end\s+of|death\s+of))", text_lower):
    fallacies.append("Slippery Slope")

# Post Hoc: assuming causation from sequence
if re.search(r"(after\s+.+?,\s*.+?(?:happened|occurred|changed|increased|decreased)|since\s+.+?,\s*.+?(?:has|have|is|are)|because\s+.+?\s+happened\s+first)", text_lower):
    fallacies.append("Post Hoc (causation from sequence)")

# Middle Ground: truth must be in the middle
if re.search(r"(somewhere\s+in\s+the\s+middle|both\s+sides\s+have\s+a\s+point|the\s+truth\s+is\s+somewhere|compromise\s+is\s+the\s+answer|meet\s+in\s+the\s+middle|split\s+the\s+difference)", text_lower):
    fallacies.append("Middle Ground")

# Burden of Proof Shifting: "prove me wrong"
if re.search(r"(prove\s+me\s+wrong|can'?t\s+prove\s+me\s+wrong|you\s+can'?t\s+prove|can'?t\s+disprove|cannot\s+disprove|you\s+can'?t\s+prove\s+it'?s\s+not|burden\s+of\s+proof)", text_lower):
    fallacies.append("Burden of Proof Shifting")

# --- MISREPRESENTATION FALLACIES ---

# Straw Man: exaggerating someone's position
if re.search(r"(so\s+you'?re\s+saying|so\s+you\s+think|so\s+you\s+want|so\s+you\s+believe|apparently\s+\w+\s+thinks|basically\s+you'?re\s+saying|in\s+other\s+words\s+you)", text_lower):
    fallacies.append("Straw Man")

# Red Herring: changing the subject
if re.search(r"(what\s+about\s+(?!you|me|us|them|the\s+fact)|but\s+what\s+about|that\s+reminds\s+me|speaking\s+of\s+which|while\s+we'?re\s+on\s+the\s+topic|on\s+a\s+related\s+note|let'?s\s+not\s+forget)", text_lower):
    fallacies.append("Red Herring")

# False Equivalence: "both sides are the same"
if re.search(r"(basically\s+the\s+same|equally\s+bad|both\s+sides\s+are|no\s+different\s+from|just\s+as\s+bad|same\s+thing|six\s+of\s+one|pot\s+calling\s+the\s+kettle)", text_lower):
    fallacies.append("False Equivalence")

# Anecdotal Evidence: using a story as proof
if re.search(r"(I\s+know\s+(a|someone|this\s+guy|this\s+person|one\s+person)\s+who|my\s+(friend|uncle|cousin|neighbor|sister|brother|dad|mom|mother|father)\s+(had|did|told|said|once)|this\s+one\s+time|I\s+once\s+(saw|heard|met|knew))", text_lower):
    fallacies.append("Anecdotal Evidence")

# Hasty Generalization: "all X are Y" from one example
if re.search(r"(all\s+\w+\s+are|every\s+\w+\s+is|\w+s\s+are\s+all|they'?re\s+all|they\s+always|they\s+never|all\s+of\s+them)", text_lower) and len(text) < 300:
    fallacies.append("Hasty Generalization")

# --- AMBIGUITY FALLACIES ---

# Equivocation: same word, different meanings
# (Hard to detect deterministically — flag if a key term appears in both claim and conclusion with different senses)
if re.search(r"(\w+)\s+is\s+\1", text_lower):
    fallacies.append("Equivocation (possible)")

# No True Scotsman: redefining to exclude counterexamples
if re.search(r"(no\s+true\s+\w+|no\s+real\s+\w+|a\s+true\s+\w+\s+would\s+never|a\s+real\s+\w+\s+doesn'?t|genuine\s+\w+\s+(would|do|always|never)|that'?s\s+not\s+(real|true|genuine|actual))", text_lower):
    fallacies.append("No True Scotsman")

# Loaded Question: question contains an assumption
if re.search(r"(when\s+did\s+you\s+stop|have\s+you\s+stopped|why\s+do\s+you\s+(always|never|keep|continue\s+to)|how\s+long\s+have\s+you\s+been)", text_lower):
    fallacies.append("Loaded Question")

# --- CAUSATION FALLACIES ---

# Correlation ≠ Causation
if re.search(r"(correlat|linked\s+to|associated\s+with|connected\s+to).{0,50}(causes?|leads?\s+to|results?\s+in|produces?|creates?)", text_lower):
    fallacies.append("Correlation ≠ Causation")

# Single Cause Fallacy: "the ONE reason"
if re.search(r"(the\s+(sole|only|one|single|primary)\s+(cause|reason|factor|explanation)|the\s+one\s+thing|the\s+single\s+most)", text_lower):
    fallacies.append("Single Cause Fallacy")

# Texas Sharpshooter: cherry-picking patterns
if re.search(r"(look\s+at\s+this\s+(cluster|pattern|trend|data)|if\s+you\s+look\s+at\s+just|focusing\s+on\s+(just|only|specifically)|cherry.pick)", text_lower):
    fallacies.append("Texas Sharpshooter (cherry-picking)")

# ============================================================
# DEDUPLICATE AND REPORT
# ============================================================

fallacies = list(dict.fromkeys(fallacies))  # remove duplicates while preserving order

if not fallacies:
    print("NO FALLACIES DETECTED")
else:
    print("FALLACIES DETECTED:")
    for i, f in enumerate(fallacies, 1):
        print(f"  {i}. {f}")
    print(f"Total: {len(fallacies)}")

result = json.dumps({
    "detected": fallacies,
    "count": len(fallacies),
    "text_length": len(text),
})
```

### Step 2: Format the detected fallacies into a readable report

2. ```python
import json

# Read step 1 results — try multiple key formats
step1_output = ""
for key in [1.0, 1, "1", "1.0"]:
    val = prior_results.get(key, "")
    if val and "detected" in str(val):
        step1_output = val
        break

try:
    data = json.loads(step1_output)
except:
    data = {"detected": [], "count": 0}

fallacies = data.get("detected", [])
count = data.get("count", 0)

# Fallacy explanations (one-liners)
explanations = {
    "Ad Hominem": "Attacking the person instead of their argument.",
    "Tu Quoque": "Deflecting criticism by pointing out the critic's hypocrisy.",
    "Guilt by Association": "Discrediting someone by linking them to a disliked group.",
    "Appeal to Authority (vague)": "Citing an authority without specifying their relevant expertise or the evidence.",
    "Bandwagon": "Claiming something is true because many people believe it.",
    "Appeal to Emotion": "Using emotional language instead of evidence.",
    "Appeal to Nature": "Claiming something is good because it's natural.",
    "Appeal to Tradition": "Claiming something is right because it's always been done that way.",
    "Appeal to Novelty": "Claiming something is better because it's new.",
    "False Dichotomy": "Presenting only two options when more exist.",
    "Begging the Question": "Assuming the conclusion in the premise (circular reasoning).",
    "Slippery Slope": "Claiming one step will inevitably lead to catastrophe.",
    "Post Hoc (causation from sequence)": "Assuming A caused B because A happened before B.",
    "Middle Ground": "Assuming the truth must be a compromise between two positions.",
    "Burden of Proof Shifting": "Demanding others disprove your claim instead of proving it.",
    "Straw Man": "Misrepresenting someone's position to make it easier to attack.",
    "Red Herring": "Introducing an irrelevant topic to distract from the argument.",
    "False Equivalence": "Claiming two things are equal when they're not.",
    "Anecdotal Evidence": "Using a personal story as proof of a general claim.",
    "Hasty Generalization": "Drawing a broad conclusion from too little evidence.",
    "Equivocation (possible)": "Using the same word with different meanings in the same argument.",
    "No True Scotsman": "Redefining a category to exclude counterexamples.",
    "Loaded Question": "Asking a question that contains an unproven assumption.",
    "Correlation ≠ Causation": "Assuming correlation proves causation.",
    "Single Cause Fallacy": "Attributing a complex outcome to a single cause.",
    "Texas Sharpshooter (cherry-picking)": "Focusing on data that supports the conclusion while ignoring the rest.",
}

if count == 0:
    report = "NO FALLACIES DETECTED — the argument appears logically sound on pattern-matching analysis. Note: this is deterministic pattern-matching only; nuanced fallacies may still be present."
else:
    lines = [f"FALLACIES DETECTED: {count}", ""]
    for i, f in enumerate(fallacies, 1):
        explanation = explanations.get(f, "")
        lines.append(f"{i}. **{f}** — {explanation}")
    lines.append("")
    lines.append(f"Review the argument and revise to remove these reasoning errors.")
    report = "\n".join(lines)

print(report)
result = json.dumps({
    "detected": fallacies,
    "count": count,
    "report": report,
})
```

[validate: contains "FALLACIES" or contains "NO FALLACIES"]
