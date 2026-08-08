---
type: procedure
model_cartridge: small
description: The argument or reasoning text to check for fallacies.
features and [llm: ] steps answer atomic yes/no questions about pre-labeled
when_to_use: >
falsifiable_if: >
inputs:
  - "name: text"
datatype: string
required: true
allowed_tools:
  - code_read
  - llm_generate
tags:
  - procedure
  - fallacy-detection
  - critical-thinking
  - small-model
  - dag
status: raw
created: 2026-08-06
summary: Detect-Fallacies (DAG Architecture)
---

# Detect-Fallacies (DAG Architecture)

## How This Works

Code steps pre-extract everything: named entities, negative language, claim/reasoning patterns, emotional words. [llm:] steps receive ONLY pre-extracted, labeled data — never raw text. Each question is about ONE specific element.

**Default rule: If unsure, answer NO.**

---

## Steps

0. ```python
import json, re

text = args.get('text', '')
text_clean = text[:500]

# Pre-extract features for the model
# 1. Named entities (capitalized names, titles)
entities = re.findall(r'\b(?:Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.)?\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b', text_clean)
entities = [e.strip() for e in entities if len(e.strip()) > 2]

# 2. Negative language patterns
negative_patterns = [
    r"(?:can'?t\s+trust|don'?t\s+trust|never|liar|lying|lies|fraud|fake|corrupt|biased|shill|hack|incompetent|stupid|idiot|moron|doesn'?t\s+care|don'?t\s+care|won'?t\s+listen)",
    r"(?:no\s+(?:real|true)\s+\w+)",
    r"(?:what\s+about\s+|but\s+what\s+about)",
    r"(?:either\s+.+?\s+or\s+|you'?re\s+either|must\s+choose)",
    r"(?:if\s+we\s+.+?\s*,\s*then\s+.+?\s*,\s*(?:and|then)\s+.+?catastroph|collapse|disaster|destroy|ruin)",
    r"(?:everyone\s+(?:knows|believes|thinks|agrees)|most\s+people|90%|99%|all\s+the)",
    r"(?:prove\s+me\s+wrong|can'?t\s+prove|cannot\s+prove|you\s+can'?t\s+prove)",
    r"(?:I\s+know\s+(?:a|someone|this\s+guy|this\s+person|one\s+person)\s+who)",
    r"(?:always\s+been|traditional|we'?ve\s+always|how\s+it'?s\s+always)",
    r"(?:natural|organic|chemical|artificial|synthetic)",
    r"(?:think\s+of\s+the\s+children|your\s+(?:children|family|kids)|terrorists?|kill\s+you|die|death|catastrophe)",
    r"(?:so\s+you'?re\s+saying|apparently\s+\w+\s+thinks|so\s+you\s+want|so\s+you\s+think)",
    r"(?:basically\s+the\s+same|equally\s+bad|both\s+sides|no\s+different)",
    r"(?:when\s+did\s+you\s+stop|have\s+you\s+stopped)",
    r"(?:the\s+(?:sole|only|one)\s+(?:cause|reason)\s+(?:of|for))",
    r"(?:because\s+it\s+says\s+so|because\s+(?:God|the\s+Bible|scripture)\s+says)",
    r"(?:I\s+(?:saw|met|know)\s+(?:one|a|this)\s+\w+\s+who|my\s+\w+\s+(?:once|always|never))",
]

negative_matches = []
for pattern in negative_patterns:
    matches = re.findall(pattern, text_clean, re.IGNORECASE)
    negative_matches.extend(matches)

# 3. Emotional language
emotion_words = re.findall(r'\b(?:fear|terrif|horror|disgust|outrage|fury|panic|devastat|tragic|innocent|helpless|victim|monster|evil|wicked|vile)\w*\b', text_clean, re.IGNORECASE)

# 4. Causal language
causal_markers = re.findall(r'\b(?:caused?\s+by|because|therefore|thus|hence|so\s+\w+\s+\w+|led\s+to|result(?:ed|ing|s)\s+(?:in|from)|due\s+to|as\s+a\s+result)\b', text_clean, re.IGNORECASE)

# 5. Check for "either/or" structure
either_or = bool(re.search(r'\b(?:either|choose\s+between|pick\s+one|only\s+two|no\s+middle\s+ground|with\s+us\s+or\s+against)', text_clean, re.IGNORECASE))

# 6. Check for question marks (loaded questions)
has_question = '?' in text_clean

# 7. Check for "no true" pattern
no_true = bool(re.search(r'\bno\s+(?:true|real)\s+\w+', text_clean, re.IGNORECASE))

# 8. Check for "I know someone who" pattern
anecdote = bool(re.search(r'\b(?:I\s+know\s+(?:a|someone|this\s+(?:guy|person))|my\s+(?:friend|uncle|cousin|grandfather|grandmother|brother|sister|neighbor|boss))\b', text_clean, re.IGNORECASE))

# 9. Check for "we've always" pattern
tradition = bool(re.search(r'\b(?:always\s+been|traditional|we\'?ve\s+always|how\s+it\'?s\s+always\s+been|for\s+(?:centuries|generations|decades))\b', text_clean, re.IGNORECASE))

# 10. Check for "natural" claims
natural_claim = bool(re.search(r'\b(?:natural|organic)\b.*\b(?:better|healthier|safer|good|right)\b', text_clean, re.IGNORECASE))

features = {
    "text": text_clean,
    "entities": entities,
    "negative_matches": negative_matches[:5],
    "emotion_words": emotion_words[:5],
    "causal_markers": causal_markers[:3],
    "either_or": either_or,
    "has_question": has_question,
    "no_true": no_true,
    "anecdote": anecdote,
    "tradition": tradition,
    "natural_claim": natural_claim,
}

result = json.dumps(features)
print(result)
```

1. [llm: Answer ONLY "YES" or "NO".

The prior step output contains pre-extracted features from a text.

Does the text contain BOTH:
- A CLAIM (something the author wants you to believe)
- REASONING (a "because" — why you should believe it)

Look at the "text" field. If it has a claim AND reasoning, answer YES. If it's just a statement of fact or an insult without reasoning, answer NO.

Examples:
- "The sky is blue." → NO
- "The sky is blue because light scatters." → YES
- "You're an idiot." → NO

Answer YES or NO.]

2. ```python
import json
step1 = prior_results[1] if len(prior_results) > 1 else ""
if isinstance(step1, dict):
    step1 = step1.get('output', '')
is_argument = 'YES' in str(step1).upper().strip()
result = json.dumps({"is_argument": is_argument})
print(result)
if not is_argument:
    print("NOT AN ARGUMENT — no fallacies to detect.")
```

3. [llm: Answer ONLY "YES" or "NO".

Look at the "entities" and "negative_matches" fields in the prior step output.

Question: Does the text mention a specific person AND say something negative about them that is used to dismiss their argument?

Answer YES only if:
- "entities" is not empty (a person is named)
- "negative_matches" is not empty (something negative is said)
- The negative thing is about the PERSON, not about their data/evidence

Non-examples:
- If negative_matches mentions "data was retracted" → NO (about data, not person)
- If entities is empty → NO (no person named)

Answer YES or NO.]

4. ```python
import json
step3 = prior_results[3] if len(prior_results) > 3 else ""
if isinstance(step3, dict):
    step3 = step3.get('output', '')
is_attack = 'YES' in str(step3).upper().strip()
result = json.dumps({"is_attack": is_attack})
print(result)
```

5. [llm: Answer ONLY "YES" or "NO" for each question below. Output one line per question.

Look at the "entities" and "negative_matches" fields.

Q1 — Ad Hominem: Is the negative statement about a PERSONAL trait (children, appearance, background, lifestyle) rather than about their work?
Example of YES: "she doesn't even have kids" — personal trait
Example of NO: "her data was falsified" — about work

Q2 — Tu Quoque: Does the text say "you do it too" — pointing out the critic's own behavior?
Look for phrases like "you also", "you did it too", "you used to"

Q3 — Genetic Fallacy: Does the text dismiss something ONLY because of where it came from?
Look for phrases like "comes from X, so it can't work"

Q4 — Poisoning the Well: Does the text discredit someone BEFORE their argument is presented?
Look for phrases like "before he speaks", "don't listen to him", "everything he says is"

Output format:
Ad Hominem: YES/NO
Tu Quoque: YES/NO
Genetic Fallacy: YES/NO
Poisoning the Well: YES/NO]

6. [llm: Answer ONLY "YES" or "NO".

Look at the "emotion_words" and "natural_claim" and "tradition" fields.

Question: Does the text use emotion, popularity, nature, or tradition INSTEAD of evidence?

Answer YES only if:
- "emotion_words" is not empty (fear/pity/anger language)
- OR "natural_claim" is true (says natural = good)
- OR "tradition" is true (says it's right because it's traditional)
- AND there is no data/statistics/evidence provided

Answer YES or NO.]

7. ```python
import json
step6 = prior_results[6] if len(prior_results) > 6 else ""
if isinstance(step6, dict):
    step6 = step6.get('output', '')
is_appeal = 'YES' in str(step6).upper().strip()
result = json.dumps({"is_appeal": is_appeal})
print(result)
```

8. [llm: Answer ONLY "YES" or "NO" for each question below. Output one line per question.

Look at the "emotion_words", "natural_claim", and "tradition" fields.

Q1 — Appeal to Authority: Does the text cite a famous person as proof when they're not an expert?
Look for a famous name in "entities" used as support for a claim outside their expertise.

Q2 — Bandwagon: Does the text say "everyone believes it" or "most people think"?
Look for phrases like "everyone knows", "most people", "90%"

Q3 — Appeal to Emotion: Are "emotion_words" present AND no evidence is given?
Words like fear, terror, horror, outrage, fury, panic, innocent children

Q4 — Appeal to Nature: Is "natural_claim" true?
The text says something is good/better because it's natural.

Q5 — Appeal to Tradition: Is "tradition" true?
The text says something is right because it's always been done.

Output format:
Appeal to Authority: YES/NO
Bandwagon: YES/NO
Appeal to Emotion: YES/NO
Appeal to Nature: YES/NO
Appeal to Tradition: YES/NO]

9. [llm: Answer ONLY "YES" or "NO".

Look at the "either_or" and "causal_markers" fields.

Question: Does the text make an unwarranted assumption?

Answer YES only if:
- "either_or" is true (presents only two options)
- OR "causal_markers" is not empty AND no evidence of causation is given
- OR the text contains circular reasoning or "prove me wrong"

Answer YES or NO.]

10. ```python
import json
step9 = prior_results[9] if len(prior_results) > 9 else ""
if isinstance(step9, dict):
    step9 = step9.get('output', '')
is_presumption = 'YES' in str(step9).upper().strip()
result = json.dumps({"is_presumption": is_presumption})
print(result)
```

11. [llm: Answer ONLY "YES" or "NO" for each question below. Output one line per question.

Look at the "either_or", "causal_markers", and "text" fields.

Q1 — Begging the Question: Is the reasoning circular? Does the reason just restate the claim?
Look for: "X is true because X" or "X is true because [something that assumes X]"

Q2 — False Dichotomy: Is "either_or" true?
The text presents only two options when more exist.

Q3 — Slippery Slope: Does the text claim a chain of events leading to catastrophe?
Look for "if...then...then...collapse/disaster/destroy" pattern.

Q4 — Post Hoc: Are "causal_markers" present AND the text assumes A caused B just because A came first?
Look for "A happened, then B happened, so A caused B" without evidence.

Q5 — Middle Ground: Does the text say the truth is "in the middle"?
Look for "somewhere in the middle", "both sides have a point", "compromise"

Q6 — Burden of Proof Shifting: Does the text say "prove me wrong"?
Look for "prove me wrong", "can't prove it's not true", "you can't disprove"

Output format:
Begging the Question: YES/NO
False Dichotomy: YES/NO
Slippery Slope: YES/NO
Post Hoc: YES/NO
Middle Ground: YES/NO
Burden of Proof Shifting: YES/NO]

12. [llm: Answer ONLY "YES" or "NO".

Look at the "text" field.

Question: Does the text distort or misrepresent something?

Answer YES only if:
- The text exaggerates someone's position ("so you're saying [extreme version]")
- OR the text changes the subject ("what about [unrelated thing]")
- OR the text uses a single story as evidence ("I know someone who...")
- OR the text treats two different things as equal

Answer YES or NO.]

13. ```python
import json
step12 = prior_results[12] if len(prior_results) > 12 else ""
if isinstance(step12, dict):
    step12 = step12.get('output', '')
is_misrep = 'YES' in str(step12).upper().strip()
result = json.dumps({"is_misrep": is_misrep})
print(result)
```

14. [llm: Answer ONLY "YES" or "NO" for each question below. Output one line per question.

Look at the "text" and "anecdote" fields.

Q1 — Straw Man: Does the text exaggerate someone's position?
Look for "so you're saying [more extreme thing]" or "apparently [name] thinks [extreme]"

Q2 — Red Herring: Does the text change the subject?
Look for "what about [different topic]" or "but what about"

Q3 — False Equivalence: Does the text say two things are "basically the same" or "equally bad"?
Look for "basically the same", "equally", "no different", "both sides"

Q4 — Anecdotal Evidence: Is "anecdote" true?
The text uses "I know someone who..." or "my friend..." as evidence.

Q5 — Hasty Generalization: Does the text conclude "all X are Y" from one example?
Look for "I met one...so all...", "one...therefore every..."

Q6 — Texas Sharpshooter: Does the text cherry-pick a pattern while ignoring the full picture?
Look for "look at this cluster" or focusing on one data point while ignoring others.

Output format:
Straw Man: YES/NO
Red Herring: YES/NO
False Equivalence: YES/NO
Anecdotal Evidence: YES/NO
Hasty Generalization: YES/NO
Texas Sharpshooter: YES/NO]

15. [llm: Answer ONLY "YES" or "NO".

Look at the "no_true" and "has_question" fields.

Question: Does the text use unclear or loaded language?

Answer YES only if:
- "no_true" is true (redefines a term: "no true X would...")
- OR "has_question" is true AND the question contains an assumption
- OR the text uses a word with two different meanings

Answer YES or NO.]

16. ```python
import json
step15 = prior_results[15] if len(prior_results) > 15 else ""
if isinstance(step15, dict):
    step15 = step15.get('output', '')
is_ambiguity = 'YES' in str(step15).upper().strip()
result = json.dumps({"is_ambiguity": is_ambiguity})
print(result)
```

17. [llm: Answer ONLY "YES" or "NO" for each question below. Output one line per question.

Look at the "no_true", "has_question", and "text" fields.

Q1 — Equivocation: Does the text use the same word with two different meanings?
Look for a word used in two different ways in the same argument.

Q2 — Composition: Does the text say "each part is X, so the whole is X"?
Look for "each...so the whole..." or "every...therefore the entire..."

Q3 — Division: Does the text say "the whole is X, so each part is X"?
Look for "the [whole] is X, so every [part] must be X"

Q4 — No True Scotsman: Is "no_true" true?
The text says "no true/real X would do Y" to dismiss a counterexample.

Q5 — Loaded Question: Is "has_question" true AND the question assumes something?
Look for "when did you stop..." or questions that assume guilt.

Output format:
Equivocation: YES/NO
Composition: YES/NO
Division: YES/NO
No True Scotsman: YES/NO
Loaded Question: YES/NO]

18. [llm: Answer ONLY "YES" or "NO".

Look at the "causal_markers" field.

Question: Does the text make an error about cause and effect?

Answer YES only if:
- "causal_markers" is not empty (the text claims causation)
- AND the causal claim is not properly supported (no mechanism, no data, just correlation or sequence)

Answer YES or NO.]

19. ```python
import json
step18 = prior_results[18] if len(prior_results) > 18 else ""
if isinstance(step18, dict):
    step18 = step18.get('output', '')
is_causation = 'YES' in str(step18).upper().strip()
result = json.dumps({"is_causation": is_causation})
print(result)
```

20. [llm: Answer ONLY "YES" or "NO" for each question below. Output one line per question.

Look at the "causal_markers" and "text" fields.

Q1 — Correlation ≠ Causation: Does the text say "X and Y happen together, so X causes Y"?
Look for two things that correlate, claimed as cause-effect without mechanism.

Q2 — Single Cause Fallacy: Does the text say "the ONE reason" or "the SOLE cause"?
Look for "the sole cause", "the only reason", "the one cause"

Q3 — Regression Fallacy: Does the text credit an intervention for a natural return to normal?
Look for "I did X and things improved" when things would have improved anyway.

Output format:
Correlation ≠ Causation: YES/NO
Single Cause Fallacy: YES/NO
Regression Fallacy: YES/NO]

21. ```python
import json

# Collect all YES answers from the fallacy-specific LLM steps
fallacy_step_indices = [5, 8, 11, 14, 17, 20]

detected = []
for idx in fallacy_step_indices:
    if idx < len(prior_results):
        raw = prior_results[idx]
        output = ""
        if isinstance(raw, dict):
            output = raw.get('output', '')
        elif isinstance(raw, str):
            output = raw
        for line in output.strip().split('\n'):
            line = line.strip()
            if ': YES' in line.upper():
                fallacy_name = line.split(':')[0].strip()
                detected.append(fallacy_name)

if not detected:
    print("NO FALLACIES DETECTED")
else:
    print("FALLACIES DETECTED:")
    for i, f in enumerate(detected, 1):
        print(f"{i}. {f}")
    print(f"Total: {len(detected)}")

result = json.dumps({"detected": detected, "count": len(detected)})
print(result)
```
