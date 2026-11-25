"""Layer 5: output guard. Runs on every final answer.

- redacts anything that looks like a credential
- removes citations that do not point at a retrieved chunk (hallucinated references)
- detects the system prompt canary (if the model echoes the hidden prompt, we know)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CANARY = "HARBOR-CANARY-7f3a"  # embedded in the system prompt; must never appear in an answer

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"pa-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"xi-[A-Za-z0-9]{24,}"),
]
_CITATION = re.compile(r"\[c(\d{1,2})\]")


@dataclass(slots=True)
class OutputVerdict:
    text: str
    redactions: int = 0
    dropped_citations: list[str] = field(default_factory=list)
    canary_leak: bool = False
    citations: list[str] = field(default_factory=list)


def guard_output(text: str, *, valid_citation_ids: set[str]) -> OutputVerdict:
    verdict = OutputVerdict(text=text)
    for pat in _SECRET_PATTERNS:
        text, n = pat.subn("[redacted]", text)
        verdict.redactions += n

    if CANARY in text:
        verdict.canary_leak = True
        text = "I can't share internal configuration, but I'm happy to help with your question."

    def _keep(m: re.Match[str]) -> str:
        cid = f"c{m.group(1)}"
        if cid in valid_citation_ids:
            if cid not in verdict.citations:
                verdict.citations.append(cid)
            return m.group(0)
        verdict.dropped_citations.append(cid)
        return ""

    text = _CITATION.sub(_keep, text)
    verdict.text = re.sub(r"[ \t]+\n", "\n", text).strip()
    return verdict
