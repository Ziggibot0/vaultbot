---
type: procedure
status: experimental
baseline: true
created: 2026-07-31
description: Remove all turn caps, read-only loop detectors, and force-synthesize nudges from chat_handler.py so VaultBot runs to completion without dying mid-task.
when_to_use: when VaultBot is dying mid-task due to turn caps or loop detectors cutting it off before completion
allowed_tools:
  - code_read
spec_version: 2
success_count: 0
failure_count: 0
summary: Remove-All-Stops
tags:
  - procedure
  - procedures
---

# Remove-All-Stops

## Steps

1. ```python
   # Read chat_handler.py and make targeted replacements:
   # 1. Change cap defaults from 25/30 to 999999
   # 2. Disable the read-only loop detector nudge
   # 3. Disable the force-synthesize nudge
   import os
   
   fpath = os.path.join("C:\\Users\\skell\\Desktop\\Vault2", "vaultbot_stuff", "vaultbot_backend", "chat_handler.py")
   
   with open(fpath, "r", encoding="utf-8") as f:
       content = f.read()
   
   changes = []
   
   # 1. Raise cap defaults to effectively unlimited
   old1 = 'os.getenv("MAX_TOOL_ROUNDS", "25")'
   new1 = 'os.getenv("MAX_TOOL_ROUNDS", "999999")'
   if old1 in content:
       content = content.replace(old1, new1)
       changes.append("MAX_TOOL_ROUNDS default 25 -> 999999")
   
   old2 = 'os.getenv("MAX_TOOL_ROUNDS_HARD_STOP", "30")'
   new2 = 'os.getenv("MAX_TOOL_ROUNDS_HARD_STOP", "999999")'
   if old2 in content:
       content = content.replace(old2, new2)
       changes.append("MAX_TOOL_ROUNDS_HARD_STOP default 30 -> 999999")
   
   old3 = 'os.getenv("MAX_TOOL_ROUNDS_SOFT_WARN", "15")'
   new3 = 'os.getenv("MAX_TOOL_ROUNDS_SOFT_WARN", "999999")'
   if old3 in content:
       content = content.replace(old3, new3)
       changes.append("MAX_TOOL_ROUNDS_SOFT_WARN default 15 -> 999999")
   
   # 2. Disable read-only loop detector: set threshold to 999999
   # The variable _MAX_READ_ONLY_STREAK controls how many consecutive
   # read-only rounds before the nudge fires. Set it very high.
   old4 = '_MAX_READ_ONLY_STREAK = '
   if old4 in content:
       # Find the line and replace whatever value it has
       lines = content.split('\n')
       for i, line in enumerate(lines):
           if line.strip().startswith('_MAX_READ_ONLY_STREAK ='):
               lines[i] = '            _MAX_READ_ONLY_STREAK = 999999  # DISABLED by Remove-All-Stops procedure'
               changes.append(f"_MAX_READ_ONLY_STREAK set to 999999 (was: {line.strip()})")
               break
       content = '\n'.join(lines)
   
   # 3. Disable force-synthesize nudge: comment out the nudge injection
   # The force-synthesize nudge injects "You MUST write a response NOW"
   # which causes the model to produce incomplete answers.
   # We'll neutralize it by setting _force_synthesize_nudged = True
   # at the start so it never fires.
   old5 = '_force_synthesize_nudged = False'
   new5 = '_force_synthesize_nudged = True  # DISABLED by Remove-All-Stops: never nudge'
   if old5 in content:
       content = content.replace(old5, new5, 1)
       changes.append("_force_synthesize_nudged disabled (set to True so nudge never fires)")
   
   # Write the file back
   with open(fpath, "w", encoding="utf-8") as f:
       f.write(content)
   
   print(f"SUCCESS: {len(changes)} changes made to {fpath}")
   for c in changes:
       print(f"  - {c}")
   
   if not changes:
       print("WARNING: No changes made - patterns not found. The file may have already been modified.")
   ```

2. ```python
   # Verify the changes by reading back the modified sections
   import os
   fpath = os.path.join("C:\\Users\\skell\\Desktop\\Vault2", "vaultbot_stuff", "vaultbot_backend", "chat_handler.py")
   
   with open(fpath, "r", encoding="utf-8") as f:
       content = f.read()
   
   checks = []
   
   if 'os.getenv("MAX_TOOL_ROUNDS", "999999")' in content:
       checks.append("PASS: MAX_TOOL_ROUNDS default is 999999")
   else:
       checks.append("FAIL: MAX_TOOL_ROUNDS default not found as 999999")
   
   if 'os.getenv("MAX_TOOL_ROUNDS_HARD_STOP", "999999")' in content:
       checks.append("PASS: MAX_TOOL_ROUNDS_HARD_STOP default is 999999")
   else:
       checks.append("FAIL: MAX_TOOL_ROUNDS_HARD_STOP default not found as 999999")
   
   if '_MAX_READ_ONLY_STREAK = 999999' in content:
       checks.append("PASS: _MAX_READ_ONLY_STREAK is 999999")
   else:
       checks.append("FAIL: _MAX_READ_ONLY_STREAK not set to 999999")
   
   if '_force_synthesize_nudged = True' in content:
       checks.append("PASS: _force_synthesize_nudged is True (disabled)")
   else:
       checks.append("FAIL: _force_synthesize_nudged not disabled")
   
   for c in checks:
       print(c)
   
   all_pass = all(c.startswith("PASS") for c in checks)
   print(f"\nOVERALL: {'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
   ```

## Related Notes

- [[Fix-Indentation]] — follow-up procedure to fix indentation errors after Remove-All-Stops edits
- [[Backend-Restart]] — typically needed after running Remove-All-Stops to apply changes
- [[VaultBot-Build-Log]] — build history tracking this modification
