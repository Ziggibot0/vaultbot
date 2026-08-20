---
type: procedure
status: experimental
baseline: true
created: 2026-08-10
summary: "Test procedure that runs Think with a false-premise question and writes intermediate results to a vault note for inspection. Verifies PREMISE_WARNINGS content and wikilink resolution."
description: "Runs the Think procedure with a false-premise architecture question, captures the full output, and writes it to a vault note for inspection. Checks that PREMISE_WARNINGS contains UNVERIFIED flags and that fabricated wikilinks are marked [UNRESOLVED: ...]."
allowed_tools:
  - run_procedure
  - vault_safe_write
tags: [procedure, test, think-procedure, false-premise, verification]
when_to_use: "when the user asks to run this procedure"
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
model_cartridge: small
---

# Test-Think-False-Premise: Verify Think Procedure False-Premise Defenses

## Purpose

This procedure tests that the Think procedure's false-premise defenses work correctly. It runs Think with a question containing a false premise about VaultBot's architecture, captures the full output, and writes it to a vault note for inspection. This is necessary because `execute_procedure` output is truncated, so intermediate results (PREMISE_WARNINGS, WIKILINKS_UNRESOLVED) can't be verified directly from the return value.

The test passes if:
1. The output contains `PREMISE_WARNINGS` with `UNVERIFIED` flags
2. The output contains `WIKILINKS_UNRESOLVED` with a count > 0
3. Fabricated wikilinks are replaced with `[UNRESOLVED: ...]` markers

## Inputs

- `question`: The false-premise question to test with (optional, defaults to a WASM question)

## Outputs

- A vault note at `vaultbot/Memory/Chat/Think-Test-Results.md` containing the full Think output
- Pass/fail verdict for each check

---

### Step 1: Run Think and Capture Results

Run the Think procedure with a false-premise question and write the full output to a vault note for inspection.

```python
import datetime

question = args.get('question', 'Since VaultBot procedures are compiled to WebAssembly for execution speed, how does the runtime handle type mismatches between Python and WASM?')

# Run the Think procedure
think_output = run_procedure('Think', args={'problem': question})

# Extract the final output text
if isinstance(think_output, dict):
    output_text = think_output.get('final_output', str(think_output))
else:
    output_text = str(think_output)

# Write the full output to a vault note for inspection
timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
note_content = f"""---
type: chat
status: raw
created: 2026-08-10
summary: "Think procedure test results for false-premise question: {question[:80]}"
tags: [test-results, think-procedure, false-premise]
---

# Think Test Results

**Test question:** {question}

**Timestamp:** {timestamp}

## Full Think Output

```
{output_text}
```

## Verification Checks

### Check 1: PREMISE_WARNINGS contains UNVERIFIED
"""

# Check 1: PREMISE_WARNINGS
has_premise_warnings = 'PREMISE_WARNINGS' in output_text
has_unverified = 'UNVERIFIED' in output_text
check1_pass = has_premise_warnings and has_unverified

note_content += f"- PREMISE_WARNINGS field present: {has_premise_warnings}\n"
note_content += f"- UNVERIFIED flag present: {has_unverified}\n"
note_content += f"- **Check 1: {'PASS' if check1_pass else 'FAIL'}**\n\n"

# Check 2: WIKILINKS_UNRESOLVED
has_wikilinks_unresolved = 'WIKILINKS_UNRESOLVED' in output_text
has_unresolved_markers = '[UNRESOLVED:' in output_text or '[UNRESOLVED: ' in output_text
check2_pass = has_wikilinks_unresolved and has_unresolved_markers

note_content += f"### Check 2: WIKILINKS_UNRESOLVED with [UNRESOLVED: ...] markers\n"
note_content += f"- WIKILINKS_UNRESOLVED field present: {has_wikilinks_unresolved}\n"
note_content += f"- [UNRESOLVED: ...] markers present: {has_unresolved_markers}\n"
note_content += f"- **Check 2: {'PASS' if check2_pass else 'FAIL'}**\n\n"

# Check 3: Confidence forced to low
has_low_confidence = 'CONFIDENCE: low' in output_text
check3_pass = has_low_confidence

note_content += f"### Check 3: Confidence forced to low\n"
note_content += f"- CONFIDENCE: low present: {has_low_confidence}\n"
note_content += f"- **Check 3: {'PASS' if check3_pass else 'FAIL'}**\n\n"

# Overall verdict
all_pass = check1_pass and check2_pass and check3_pass
note_content += f"## Overall Verdict: {'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}\n"

# Write the note
vault_safe_write('vaultbot/Memory/Chat/Think-Test-Results.md', note_content)

# Print summary
result = f"Check 1 (PREMISE_WARNINGS): {'PASS' if check1_pass else 'FAIL'}\n"
result += f"Check 2 (WIKILINKS_UNRESOLVED): {'PASS' if check2_pass else 'FAIL'}\n"
result += f"Check 3 (Confidence low): {'PASS' if check3_pass else 'FAIL'}\n"
result += f"Overall: {'ALL PASSED' if all_pass else 'SOME FAILED'}\n"
result += f"Full results written to: vaultbot/Memory/Chat/Think-Test-Results.md"
print(result)
```

[validate: contains "Check 1"]
[validate: contains "Overall"]

---

## Related

- [[Think]] — the procedure being tested
- [[Chat-Think-Procedure-False-Premise-Vulnerability]] — documentation of the vulnerability and fixes