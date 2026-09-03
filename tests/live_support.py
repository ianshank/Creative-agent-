"""How the live-marked tests decide whether they can run (DEC-F21).

The predicate used to be `os.environ.get("ANTHROPIC_API_KEY")`. That is a proxy for the
capability, not the capability, and it is wrong in the direction that hurts: the Claude
Agent SDK does not require an API key. It spawns the `claude` CLI, which authenticates
from whatever credential that CLI holds — an API key, or an OAuth/subscription session.

The consequence was not theoretical. In an environment where a live SDK call demonstrably
succeeds, the live leg reported "no API key" and skipped, and the roadmap recorded
"blocked on an API key" as fact. Behind that skip sat a defect that made every real review
fail (DEC-F20). A gate that reports success while verifying nothing is worse than no gate,
because it is counted.

So the predicate below asks whether *some* usable credential path exists, and the failure
direction is deliberate: if a credential is present but unusable, the test fails rather
than skipping. A skip is a claim that the check could not be run; a failure is a claim
that it ran and did not pass. Only the first is honest when no credential exists at all.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache

SKIP_REASON = (
    "no usable Claude Agent SDK credential: set ANTHROPIC_API_KEY, or authenticate the "
    "`claude` CLI (the SDK spawns it and inherits its session). Gating on the API key "
    "alone skipped this test in environments where a live call works — see DEC-F21."
)


@lru_cache(maxsize=1)
def sdk_credential_available() -> bool:
    """True when some credential path the SDK can use is present.

    Two, because the SDK accepts two. `ANTHROPIC_BASE_URL` alone is not one of them: a
    proxy address says where to send a request, not what authorises it.

    Deliberately not a probe call. Making the predicate itself an API call would mean a
    network blip reads as "cannot check" and skips — reintroducing the decorative gate
    this replaces, with a more expensive implementation.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or shutil.which("claude") is not None
