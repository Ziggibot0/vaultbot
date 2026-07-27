# Python exec function with restricted globals namespace — how to safely execute untrusted code using exec() with a custom globals dict, restrict builtins, inject helper functions, and capture return values. Also patterns for parent-child process communication via pipes or JSON over stdin/stdout.

## Summary
Research into 'Python exec function with restricted globals namespace — how to safely execute untrusted code using exec() with a custom globals dict, restrict builtins, inject helper functions, and capture return values. Also patterns for parent-child process communication via pipes or JSON over stdin/stdout.' (14 sources, 16 facts).

## Key Findings
- Issue to ask for and discuss about new profiles.  [sources: Profile requests]
- Argoexec manages the execution of tasks in workflow pods.  [sources: Argo executor]
- New hook/component tests cover keep-previous-data, quiet loading, quiet empty, and quiet transient error, and fail on the pre-fix shared component path.  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- 2. **API contract drift gate** — AST-based structural comparison between `apps/.../shared/api.ts` and `libs/smart-suggest/client/src/api.ts` (incl. mirrored error schemas) via `pnpm --dir apps/smart-suggest api:drift:check` (also wired into `api:check`).  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- Blank `countryCodes` now rejected consistently on both surfaces.  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- 3. **Effect patch/workspace consistency** — Effect patch hoisted to the root workspace (`patches/effect-schema-error-type-id.patch` + `pnpm-workspace.yaml`), so root consumers of `libs/smart-suggest/*` resolve patched Effect identically to the nested workspace.  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- 4. **Install-time churn** — `oxfmt` removed from `postinstall`; installs no longer rewrite source.  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- 5. **Rate limiter / provider budget safety** — inbound limiter keyed primarily by `cf-connecting-ip` (Origin no longer identity), bucket eviction by earliest `resetAt`, per-isolate limits documented, and a provider outbound budget gate (`SMART_SUGGEST_PROVIDER_OUTBOUND_BUDGET_MAX`, default `0`) keeps paid providers disabled until explicitly budgeted.  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- Tests cover origin-rotation bypass, shared-origin self-DoS avoidance, eviction, and budget exhaustion.  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- Also included from the follow-up list: - **Real SQLite storage integration tests** — `node:sqlite` + FTS5 suite runs the real drizzle migrations and exercises FTS prefix s  [sources: Smart Suggest owned data pipeline and UltraModern checkout]
- The second part demonstrates safer execution with restricted globals.  [sources: Python exec Function - Complete Guide - ZetCode]
- After execution, the variables x and y are available in the current namespace, demonstrating exec's ability to modify the environment.  [sources: Python exec Function - Complete Guide - ZetCode]
- As classes need a namespace to be defined in.  [sources: Basic usage - RestrictedPython 8.2 documentation]
- How to Use It pip install RestrictedPython Basic Example from RestrictedPython import compile_restricted from RestrictedPython import safe_globals source_code = """ def example(): return 'Hello World!' """ loc = {} byte_code = compile_restricted(source_code, '<inline>', 'exec') exec(byte_code, safe_globals, loc) loc['example']() Output Hello World!  [sources: ParselTongue - Making exec safer using RestrictedPython]
- The Python exec() takes three parameters: code which is the compiled byte code globals which is global dictionary locals which is the local dictionary By limiting the entries in the globals and locals dictionaries you restrict the access to the available library modules and methods.  [sources: Basic usage - RestrictedPython 8.2 documentation]

## Sources
- [Profile requests](https://github.com/netblue30/firejail/issues/1139) ([[learningMaterial/web/github-com-netblue30-firejail-issues-1139-453354ec.html|archived]])
- [Argo executor](https://hub.docker.com/r/dhi/argoexec)
- [Smart Suggest owned data pipeline and UltraModern checkout](https://github.com/TechsioCZ/new-engine/pull/477) ([[learningMaterial/web/github-com-techsiocz-new-engine-pull-477-6b482efe.html|archived]])
- [atlassian/pipelines-kubernetes-namespace-expiry](https://hub.docker.com/r/atlassian/pipelines-kubernetes-namespace-expiry) ([[learningMaterial/web/hub-docker-com-r-atlassian-pipelines-kubernetes-namespace-expiry-8eb1f52b.html|archived]])
- [[DRAFT]: LayerZero Message Processing](https://github.com/canopy-network/tanssi/pull/1442) ([[learningMaterial/web/github-com-canopy-network-tanssi-pull-1442-758decdc.html|archived]])
- [Python's exec (): Execute Dynamically Generated Code](https://realpython.com/python-exec/) ([[learningMaterial/web/realpython-com-python-exec-5a76e1cb.html|archived]])
- [Running Untrusted Python Code — Andrew Healey](https://healeycodes.com/running-untrusted-python-code) ([[learningMaterial/web/healeycodes-com-running-untrusted-python-code-4d45ce8b.html|archived]])
- [Basic usage - RestrictedPython 8.2 documentation](https://restrictedpython.readthedocs.io/en/latest/usage/basic_usage.html) ([[learningMaterial/web/restrictedpython-readthedocs-io-en-latest-usage-basic-usage-html-46b5acd3.html|archived]])
- [Python exec Function - Complete Guide - ZetCode](https://zetcode.com/python/exec-builtin/) ([[learningMaterial/web/zetcode-com-python-exec-builtin-1caf3042.html|archived]])
- [exec () | PyGuides](https://pyguides.dev/reference/built-in-functions/exec/) ([[learningMaterial/web/pyguides-dev-reference-built-in-functions-exec-e47da53c.html|archived]])
- [ParselTongue - Making exec safer using RestrictedPython](https://parseltongue.co.in/making-exec-safer-using-restrictedpython/) ([[learningMaterial/web/parseltongue-co-in-making-exec-safer-using-restrictedpython-7ce654b0.html|archived]])
- [Usage Guide | zopefoundation/RestrictedPython | DeepWiki](https://deepwiki.com/zopefoundation/RestrictedPython/3-usage-guide) ([[learningMaterial/web/deepwiki-com-zopefoundation-restrictedpython-3-usage-guide-5d5a07ba.html|archived]])
- [GitHub - zopefoundation/RestrictedPython: A restricted execution ...](https://github.com/zopefoundation/RestrictedPython) ([[learningMaterial/web/github-com-zopefoundation-restrictedpython-1e872f24.html|archived]])
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])

## Follow-up Queries (gap fill)
- Python exec function with restricted globals namespace — how to safely execute untrusted code using exec() with a custom globals dict, restrict builtins, inject helper functions, and capture return values. Also patterns for parent-child process communication via pipes or JSON over stdin/stdout. works mechanism
- Python exec function with restricted globals namespace — how to safely execute untrusted code using exec() with a custom globals dict, restrict builtins, inject helper functions, and capture return values. Also patterns for parent-child process communication via pipes or JSON over stdin/stdout. globals
- Python exec function with restricted globals namespace — how to safely execute untrusted code using exec() with a custom globals dict, restrict builtins, inject helper functions, and capture return values. Also patterns for parent-child process communication via pipes or JSON over stdin/stdout. communication

<!-- research: 14 sources, 16 facts, 2 rounds -->

## Related

[[Procedure-Subprocess-Architecture]]
