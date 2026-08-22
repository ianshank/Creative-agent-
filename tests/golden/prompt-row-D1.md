Doctrine sweep — row D1 only. Verdict: hit (artifact satisfies the check),
miss (it violates it — emit findings), or not_applicable (with a mandatory na_reason).

### D1 (tier PR)
Principle: A principle
Sources: Doe, A Paper, Journal 2020 [arXiv:2001.00001]
Check licensed: A licensed check
Failure caught: A failure mode


Rules for this row:
- Apply exactly the check licensed above; do not import checks from other rows.


- Findings need: severity, a short stable `anchor` phrase naming the defect site,
  doctrine_refs ["D1"], and supports listing every basis.
- Every doctrinal assertion needs a verification_entries item mapping it to this row and
  a source URL (fetched=true only if you fetched it in this session).
- Reuse anchors from prior cycles when the same defect persists:
  - D1+some-defect


Artifact mode: conformance | class: architecture_design

<<<ARTIFACT-UNDER-REVIEW
(example)
END-ARTIFACT>>>
