---
verification:
  total_claims: 3
  verified: 0
  unsupported: 0
  contradicted: 0
  unsourced: 3
  source_not_found: 0
  last_verified: "2026-07-27T02:53:41.164830Z"
---
# FAISS IndexIDMap2 remove_ids

## Summary
Deep research into 'FAISS IndexIDMap2 remove_ids' (6 sources, 14 facts).

## Key Findings
- Faiss is written in C++ with complete wrappers for Python/numpy.  [sources: openeuler/faiss]
- So ingest adds to FAISS before committing SQLite (compensating on rollback), while delete commits SQLite first.  [sources: fix(database): allocate FAISS ids monotonically, never from index.ntotal]
- Where to get help: openEuler CloudNative SIG ⁠ , openEuler ⁠ . ⁠ Faiss | openEuler Faiss is a library for efficient similarity search and clustering of dense vectors.  [sources: openeuler/faiss]
- Learn more on Faiss Documentation ⁠ . ⁠ Supported tags and respective Dockerfile links The tag of each faiss docker image is consist of the version of faiss and the version of basic image.  [sources: openeuler/faiss]
- Governing rule adopted for the SQLite/FAISS boundary: **prefer orphan vectors, never dangling rows.** Orphans are swept for free by `repair`; dangling rows need re-embedding.  [sources: fix(database): allocate FAISS ids monotonically, never from index.ntotal]
- Two chunks then shared one vector, and because `_search.py` builds a `faiss_id -> row` dict, the later row won — so queries returned **the wrong document, silently, at a perfect similarity score**.  [sources: fix(database): allocate FAISS ids monotonically, never from index.ntotal]
- A failed save no longer leaves a stray `.tmp`, and an emptied index is now written out instead of leaving a stale file on disk. - On open, a duplicate `faiss_id` refuses to load and points at `repair`.  [sources: fix(database): allocate FAISS ids monotonically, never from index.ntotal]
- Back Back openeuler/faiss Sponsored OSS By openeuler • Updated 17 days ago Image 0 1.2K Overview Tags openeuler / faiss repository overview ⁠ Quick reference The official faiss docker image.  [sources: openeuler/faiss]
- Tag summary Recent tags 20180223-oe2403sp4 Recent tags Content type Image Digest sha256:168a93f25 … Size 522.5 MB Last updated 17 days ago docker pull openeuler/faiss:20180223-oe2403sp4 Copy This week's pulls Pulls: 421 Jul 13 to Jul 19 Learn more ⁠  [sources: openeuler/faiss]
- Five providers already implement upsert semantics (PGVector, Qdrant, Elasticsearch, OCI 26AI, Infinispan), but the remaining five use pure insert — causing **silent data duplication** (FAISS, Milvus, Weaviate), **errors** (ChromaDB), or **inconsistent internal state** (SQLite-vec) when chunks with existing IDs are re-inserted.  [sources: `insert_chunks` should use upsert semantics — providers silently duplicate or error on repeated chunk IDs]
- It reads the saved embedding config directly, so it can open a database whose embedding provider is unreachable. ## Tests `tests/test_faiss_sqlite_invariant.py` is the regression net: the dual-store invariant asserted after every mutating op across upsert/update/delete interleavings, plus retrieval-correctness-after-mutation against a **real** FAISS index.  [sources: fix(database): allocate FAISS ids monotonically, never from index.ntotal]
- Verify faiss installation docker run --rm openeuler/faiss:{Tag} -c "import faiss; print(faiss.__version__)" Copy To get an interactive shell docker exec -it my-faiss /bin/bash Copy ⁠ Question and answering If you have any questions or want to use some special features, please submit an issue or a pull request on openeuler-docker-images ⁠ .  [sources: openeuler/faiss]
- But `remove_ids` **decrements** `ntotal`, so it is not a high-water mark.  [sources: fix(database): allocate FAISS ids monotonically, never from index.ntotal]

## Sources
- [`insert_chunks` should use upsert semantics — providers silently duplicate or error on repeated chunk IDs](https://github.com/ogx-ai/ogx/issues/6256) ([[learningMaterial/web/github-com-ogx-ai-ogx-issues-6256-a87839c8.html|archived]])
- [vdaas/vald-agent-faiss](https://hub.docker.com/r/vdaas/vald-agent-faiss) ([[learningMaterial/web/hub-docker-com-r-vdaas-vald-agent-faiss-12070e77.html|archived]])
- [M30: pluggable VectorIndex + local FAISS backend (Phase 3's first milestone)](https://github.com/jweter/knowledge-engine-core/pull/154) ([[learningMaterial/web/github-com-jweter-knowledge-engine-core-pull-154-0117f976.html|archived]])
- [intel/vector-retriever-faiss](https://hub.docker.com/r/intel/vector-retriever-faiss) ([[learningMaterial/web/hub-docker-com-r-intel-vector-retriever-faiss-d1904d3f.html|archived]])
- [fix(database): allocate FAISS ids monotonically, never from index.ntotal](https://github.com/thomas-villani/localvectordb/pull/34) ([[learningMaterial/web/github-com-thomas-villani-localvectordb-pull-34-9c445953.html|archived]])
- [openeuler/faiss](https://hub.docker.com/r/openeuler/faiss) ([[learningMaterial/web/hub-docker-com-r-openeuler-faiss-c9703633.html|archived]])

## Follow-up Queries (gap fill)
- faiss indexidmap2 indexidmap2 remove_ids faiss example such as
- faiss indexidmap2 indexidmap2 remove_ids faiss FAISS IndexIDMap2

<!-- research: 6 sources, 14 facts, 3 rounds -->