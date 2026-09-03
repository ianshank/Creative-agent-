"""Deterministic half of the source-quality protocol.

Cluster citations, bibliography hygiene, and vendor-domain detection are mechanical text
checks; the judgement half (load-bearing mapping, regime breaks) is a SOURCE_QUALITY LLM
call assembled elsewhere. All thresholds and severities come from oracle data.
"""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlparse

from creative_agent.harness.canonical import canonicalize
from creative_agent.harness.policy import URL_PATTERN
from creative_agent.models.findings import SupportRef
from creative_agent.models.oracle import SourceQualityConfig
from creative_agent.models.sweeps import CandidateFinding

_REF_TOKEN = r"\[\d{1,4}\]"  # noqa: S105 — a citation-marker regex, not a secret
# Shared with the allowlist harvester (DEC-F25): the checker that judges a bibliography
# and the harvester that decides what the session may fetch must agree on what a URL is,
# or a citation is judged by one and unreachable by the other.
_URL = URL_PATTERN


class SourceQualityChecker:
    """Runs the mechanical source-quality checks over the raw artifact text."""

    def __init__(self, config: SourceQualityConfig) -> None:
        self._config = config
        # Built from the configured threshold so the pre-filter can never disagree with
        # the rule it screens for.
        self._cluster_run = re.compile(
            rf"(?:{_REF_TOKEN}\s*){{{config.cluster_citation_min_refs},}}"
        )

    def cluster_citations(self, text: str) -> list[CandidateFinding]:
        """Runs of numbered refs at/beyond the threshold are unreviewable (spec: Major)."""
        candidates: list[CandidateFinding] = []
        for match in self._cluster_run.finditer(text):
            refs = re.findall(_REF_TOKEN, match.group(0))
            if len(refs) >= self._config.cluster_citation_min_refs:
                cluster = "".join(refs)
                candidates.append(
                    CandidateFinding(
                        severity=self._config.cluster_citation_severity,
                        summary=(
                            f"Cluster citation {cluster} is unreviewable; require "
                            "sentence-level mapping of claims to sources."
                        ),
                        anchor=f"cluster-citation-{cluster}",
                        supports=[SupportRef(kind="gate_failure", ref="source_quality")],
                        disposition_required="Map each sentence to its source.",
                    )
                )
        return candidates

    def bibliography_hygiene(self, text: str) -> list[CandidateFinding]:
        """Duplicate references: one canonical work appearing under multiple URLs."""
        by_canonical: dict[str, set[str]] = defaultdict(set)
        for match in _URL.finditer(text):
            url = match.group(0).rstrip(".,;")
            canonical = canonicalize(url)
            if canonical:
                by_canonical[canonical].add(url)
        candidates: list[CandidateFinding] = []
        for canonical, urls in sorted(by_canonical.items()):
            if len(urls) > 1:
                candidates.append(
                    CandidateFinding(
                        severity=self._config.bibliography_hygiene_severity,
                        summary=(
                            f"Bibliography hygiene: {canonical} appears under "
                            f"{len(urls)} distinct URLs."
                        ),
                        anchor=f"duplicate-source-{canonical}",
                        supports=[SupportRef(kind="gate_failure", ref="source_quality")],
                        disposition_required="Deduplicate the bibliography.",
                    )
                )
        return candidates

    def vendor_urls(self, text: str) -> list[str]:
        """URLs on configured vendor domains — surfaced with the labelling note."""
        hits: list[str] = []
        for match in _URL.finditer(text):
            url = match.group(0).rstrip(".,;")
            host = (urlparse(url).hostname or "").lower()
            if any(host == d or host.endswith(f".{d}") for d in self._config.vendor_domains):
                hits.append(url)
        return sorted(set(hits))

    def vendor_findings(self, text: str) -> list[CandidateFinding]:
        urls = self.vendor_urls(text)
        if not urls:
            return []
        return [
            CandidateFinding(
                severity=self._config.vendor_page_severity,
                summary=(
                    f"Vendor page(s) cited ({', '.join(urls[:5])}"
                    + (", …" if len(urls) > 5 else "")
                    + f"): {self._config.vendor_page_note}"
                ),
                anchor="vendor-pages-cited",
                supports=[SupportRef(kind="gate_failure", ref="source_quality")],
                disposition_required=(
                    "Label vendor material as advertising, or replace it with archival evidence."
                ),
            )
        ]

    def check(self, text: str) -> list[CandidateFinding]:
        return [
            *self.cluster_citations(text),
            *self.bibliography_hygiene(text),
            *self.vendor_findings(text),
        ]
