# Codebase Map Search Protocol

This document tells Legion commands (and agents) how to query the generated map dataset
and then read source files before acting. Semantic search here is **retrieval over map
metadata plus source reads** — no embeddings, vector DB, API keys, or external services.

## Required Artifacts

- `.planning/CODEBASE.md` — human-readable architecture, conventions, risks, runbook.
- `.planning/codebase/index.jsonl` — one JSON object per retrievable chunk (this is the primary search target).
- `.planning/codebase/symbols.json` — coarse entry points, APIs, modules, tests, config, dependencies, ownership, risk areas.
- `.planning/config/directory-mappings.yaml` — directory category mappings for placement.

> Companion: `.repo_context/` holds the repository's own curated, source-backed docs and
> playbooks. Use `.repo_context/index.md` to route from intent → smallest relevant doc.

## Query Planning (Section 18.1)

Normalize the request into:

```
query = {
  terms:        important nouns/verbs/feature names/tech names (e.g. "object cache get latency"),
  path_hints:   explicit files/directories mentioned,
  symbol_hints: classes/functions/services (e.g. ObjectClientImpl, OCMetadataManager, worker_object.proto),
  domain_hints: likely domains — object_cache, stream_cache, kv_cache, cluster, hash_ring,
                etcd/metastore, l2cache, slot_recovery, rdma, shared_memory, rpc, logging, metrics
}
```

## Retrieval Order (Section 18.2)

1. Match explicit `path_hints` against `path` fields in `index.jsonl` and `symbols.json`.
2. Match `symbol_hints` against `symbols`/`apis`/`modules` in `symbols.json`.
3. Match `terms`/`domain_hints` against `keywords` and `aliases` in `index.jsonl`.
4. Scan `CODEBASE.md` section headings (Functionality Inventory, Module Ownership, Risk Areas, API Surface) for broad context.
5. **Read the original source files** for the top matches before writing plans, reviews, or code.

Use Grep/Read over the JSONL and source — e.g.:

```
grep -i "slot recovery" .planning/codebase/index.jsonl
grep -i "urma_manager" .planning/codebase/symbols.json
```

## Ranking (Section 18.3)

Rank by: exact path/symbol match → keyword/alias overlap → same domain as the request →
risk level and fan-in relevance → git-hotspot recency (see Risk Areas in CODEBASE.md).
Return at most 5 primary chunks and 5 "read next" source paths unless broader analysis is requested.

## Read-Before-Acting Requirement

- Chunk summaries are **not** source of truth for edits — read the cited file/lines first.
- Do not cite stale map data as current without checking freshness (`/legion:map --check`).
- Do not load the entire index into an agent prompt when a targeted query suffices.
- If query results conflict with current source, **current source wins** — refresh the map (`/legion:map --refresh`).
- This is a hot-path infrastructure system: confirm hot-path / recovery implications in
  `.repo_context/modules/overview/engineering-principles.md` before proposing changes.

## Example

Command:

```
/legion:map --query "object cache get latency hot path"
```

Expected output:

```markdown
## Map Search Results

| Rank | Chunk | Path | Lines | Kind | Why it matched |
|------|-------|------|-------|------|----------------|
| 1 | map:worker-oc-get:001 | src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp | 1-3147 | module | terms "object cache get"; domain object_cache; high-risk hot path |
| 2 | map:client-object-impl:001 | src/datasystem/client/object_cache/object_client_impl.cpp | 1-4132 | module | symbol ObjectClientImpl; "get"; top hotspot |
| 3 | map:rdma-transport:001 | src/datasystem/common/rdma/urma_manager.cpp | 1-400 | module | "latency"/data-movement path |

### Read Next
- `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp` (get path)
- `src/datasystem/client/object_cache/object_client_impl.cpp` (client Get/Put)
- `.repo_context/modules/overview/engineering-principles.md` (hot-path rules)
- `src/datasystem/protos/worker_object.proto` (RPC contract)
```
