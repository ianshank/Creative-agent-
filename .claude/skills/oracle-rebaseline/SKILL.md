---
name: oracle-rebaseline
description: >
  Re-baseline a review oracle's doctrine sources: resolve every row to a DOI/arXiv id,
  diff author lists mechanically, and update freshness metadata. Use when asked to
  "rebaseline", verify doctrine sources, check oracle freshness, or when `oracles
  validate`/review reports flag unverified or stale rows.
---

# Oracle re-baselining

The doctrine table is data whose citations rot; unverified rows get severity-capped by
the freshness rule. v1 of the sutton spec shipped three fabricated author lists — the
mechanical author-list diff below is the defense.

## Procedure

1. Dry-run first and read every line:

   ```bash
   creative-agent oracles rebaseline sutton --dry-run
   ```

2. `AUTHOR MISMATCH` lines are the serious case: the YAML's `authors` disagree with
   arXiv. Fetch the arXiv abstract page yourself (WebFetch, or the alphaXiv MCP tools if
   available) to confirm, then fix the `authors`/`citation` in
   `src/creative_agent/data/oracles/sutton.v2.yaml` — never delete the row or soften the
   check.
3. `unreachable`/`skipped` sources (no arXiv id — DOIs, author-hosted PDFs, talks):
   resolve manually with WebFetch. If a URL is confirmed, update `url` and set
   `verified: true` with `last_verified: <today>`; if it cannot be resolved, leave it
   unverified — the staleness cap doing its job is the design, not a failure.
4. Apply the automated updates:

   ```bash
   creative-agent oracles rebaseline sutton
   ```

   The rewrite drops YAML comments — review `git diff` and restore header comments if
   needed, then run `creative-agent oracles validate sutton` and `uv run pytest
   tests/unit/test_oracle_loader.py`.
5. The shipped-oracle invariant tests (author lists, row count) are tripwires: if a
   legitimate correction breaks one, update the test AND add a `docs/decision-log.md`
   entry recording the correction — that friction is intentional.
6. Commit the YAML, test, and decision-log changes together with a message naming which
   rows changed.
