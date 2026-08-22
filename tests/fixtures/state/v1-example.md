---
schema_version: 1
artifact_id: v1-example
cycle: 2
history:
- cycle: 1
  completed_at: '2026-08-01T10:00:00Z'
  mode: conformance
  artifact_class: architecture_design
  content_sha256: 0a1b2c3d
  findings:
  - key:
      row_id: D2
      slug: no-preference-relation
    severity: 2
    disposition: open
    summary: No preference relation stated for the scalar reward claim.
- cycle: 2
  completed_at: '2026-08-08T10:00:00Z'
  mode: conformance
  artifact_class: architecture_design
  content_sha256: 1b2c3d4e
  findings:
  - key:
      row_id: D2
      slug: no-preference-relation
    severity: 2
    disposition: addressed
    summary: Preference relation added; axioms partially tested.
---

## Review log — v1-example

Frozen v1-format fixture. Every future state schema version must keep loading this file
(migrate-on-read); see DEC-F4.
