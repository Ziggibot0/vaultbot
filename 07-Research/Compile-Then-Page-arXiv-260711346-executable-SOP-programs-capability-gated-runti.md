# Compile Then Page arXiv 2607.11346 executable SOP programs capability-gated runtime — how does the compilation step work, what is the PG stack machine architecture, how does frame paging work, what are the empirical results compared to free-form prompting

## Summary
Research into 'Compile Then Page arXiv 2607.11346 executable SOP programs capability-gated runtime — how does the compilation step work, what is the PG stack machine architecture, how does frame paging work, what are the empirical results compared to free-form prompting' (8 sources, 15 facts).

## Key Findings
- This work proposes architectural and software innovations to provide the greatest scalability to date for running graph algorithms while still being programmable for other domains.  [sources: Dalorex: A Data-Local Program Execution and Architecture for Memory-bound Applications]
- While prior work with prefetching, decoupling, or pipelining can mitigate memory latency and improve core utilization, memory bottlenecks persist due to limited off-chip bandwidth.  [sources: Dalorex: A Data-Local Program Execution and Architecture for Memory-bound Applications]
- That PR is sitting at ~100 review comments and is gfx12-only, so rather than force-push another rewrite on top of it I opened a fresh one.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- This picks up the two things that actually blocked it: - @comfyanonymous on [#72](https://github.com/Comfy-Org/comfy-kitchen/pull/72#issuecomment-4953900342): > We need to figure out how to package this in the current comfy-kitchen wheels, I don't want to have separate wheels for nvidia/amd/intel/etc...  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- This builds one wheel with both backends in it, and keeps the `abi3` tag while doing it. - [#72](https://github.com/Comfy-Org/comfy-kitchen/pull/72) only ran on RDNA4.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- This adds RDNA3, RDNA3.5 and RDNA2.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- The kernels are #72's, rebased on current main.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- The packaging follows the approach in [dev/hip1](https://github.com/Comfy-Org/comfy-kitchen/tree/dev/hip1) by @rattus128. ### Packaging `setup.py` detects both toolchains and emits `backends/cuda/_C*` and `backends/hip/_C*` into a single wheel.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- The Linux job installs CUDA and ROCm into the same manylinux container; the Windows job installs the ROCm toolchain from AMD's release directory (https://repo.radeon.com/), the same host the Linux job takes its RPMs from. `auditwheel` excludes the ROCm runtime libs the same way it already excludes the CUDA ones, and both jobs fail if the wheel does not contain both extensions.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- Each backend withdraws itself at import when its runtime is missing, so a CUDA-only or ROCm-only machine gets the same wheel and just sees one backend register.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- The HIP extension is built against the Python limited API on 3.12+, like the CUDA one.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- That matters for the packaging goal: an extension that is not abi3 drags the whole wheel back to version-specific builds, so adding AMD would otherwise have multiplied the wheel matrix.  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]
- AWQ GEMV; WMMA GEMMs decline | `v_wmma_*_w32_gfx12` has no gfx11 encoding, and the difference is not only the intrinsic name: gfx12 splits a K-step across the half-waves and takes 8-byte operands, while gfx11 gives each lane the whole K-step and duplicates it across the half-waves (rocWMMA calls this input duplication).  [sources: Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels]

## Sources
- [Add HIP backend for AMD RDNA2-RDNA4 to the existing wheels](https://github.com/Comfy-Org/comfy-kitchen/pull/74) ([[learningMaterial/web/github-com-comfy-org-comfy-kitchen-pull-74-ea4b7f48.html|archived]])
- [Compile, Then Page: Executable SOP Programs and a Capability-Gated ...](https://arxiv.org/pdf/2607.11346) ([[learningMaterial/web/arxiv-org-pdf-2607-11346-42e4a37b.html|archived]])
- [project discord](https://chat.marginalia.nu) ([[learningMaterial/web/chat-marginalia-nu-0b49d55a.html|archived]])
- [Dalorex: A Data-Local Program Execution and Architecture for Memory-bound Applications](https://arxiv.org/abs/2207.13219v4) ([[learningMaterial/web/arxiv-org-abs-2207-13219v4-7e9a4cad.html|archived]])
- [ops: loop-lane telemetry — running per-lane status (babysit / triage / work)](https://github.com/melodic-software/claude-code-plugins/issues/502) ([[learningMaterial/web/github-com-melodic-software-claude-code-plugins-issues-502-165b78a5.html|archived]])
- [Compile, Then Page: Executable SOP Programs and a Capability-Gated Runtime for Procedural LLM Agents](https://arxiv.org/abs/2607.11346v3) ([[learningMaterial/web/arxiv-org-abs-2607-11346v3-11edb704.html|archived]])
- [Multi-messenger Observations of a Binary Neutron Star Merger](https://arxiv.org/abs/1710.05833v2) ([[learningMaterial/web/arxiv-org-abs-1710-05833v2-8d50ea07.html|archived]])
- [Verified Secure Compilation for Mixed-Sensitivity Concurrent Programs](https://arxiv.org/abs/2010.14032v2) ([[learningMaterial/web/arxiv-org-abs-2010-14032v2-b059a50c.html|archived]])

## Follow-up Queries (gap fill)
- Compile Then Page arXiv 2607.11346 executable SOP programs capability-gated runtime — how does the compilation step work, what is the PG stack machine architecture, how does frame paging work, what are the empirical results compared to free-form prompting capability-gated
- Compile Then Page arXiv 2607.11346 executable SOP programs capability-gated runtime — how does the compilation step work, what is the PG stack machine architecture, how does frame paging work, what are the empirical results compared to free-form prompting compilation
- Compile Then Page arXiv 2607.11346 executable SOP programs capability-gated runtime — how does the compilation step work, what is the PG stack machine architecture, how does frame paging work, what are the empirical results compared to free-form prompting executable

<!-- research: 8 sources, 15 facts, 2 rounds -->

## Related

[[Procedure-Subprocess-Architecture]]
[[Procedural-Bootstrap-and-Evolution-Plan]]
[[Deterministic-Scaffolding-for-Small-Models]]
