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

The predicate below is still a proxy — `shutil.which` proves the CLI is installed, not that
it holds a session, and nothing short of a real call can prove that. What changed is the
direction it is wrong in. An unauthenticated `claude` on PATH makes the live tests **run and
fail**, which is loud and true; the old predicate made a working environment **skip**, which
is quiet and false. A skip claims the check could not be run; a failure claims it ran and
did not pass. Only the first is honest when no credential path exists at all, and only a
proxy that errs toward failing keeps that claim honest.
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
    """True when a credential path the SDK *could* use is present — not that it works.

    Two paths, because the SDK accepts two. `ANTHROPIC_BASE_URL` alone is not one of them:
    a proxy address says where to send a request, not what authorises it.

    `shutil.which` is explicitly a cheap proxy: it finds the executable, and an installed
    but unauthenticated `claude` returns True here. That is the intended reading — the live
    tests then run and fail with the SDK's own authentication error, which names the real
    problem, rather than skipping and reporting "cannot check" about an environment that
    was never checked.

    Deliberately not a probe call. Making the predicate itself an API call would mean a
    network blip reads as "cannot check" and skips — reintroducing the decorative gate
    this replaces, with a more expensive implementation.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or shutil.which("claude") is not None
