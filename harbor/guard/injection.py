"""Layer 1 of prompt-injection defence: cheap, deterministic pattern scoring.

Runs on every user turn and on every chunk at ingestion time (indirect injection through documents).
Scores are additive and capped at 1.0; the policy in the guard node decides what to do with them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# (pattern, weight, label)
_RULES: list[tuple[re.Pattern[str], float, str]] = [
    (
        re.compile(
            r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?|guidance)",
            re.I,
        ),
        0.8,
        "override_instructions",
    ),
    (
        re.compile(
            r"disregard (your|the|all) (system|previous|prior|earlier) (prompt|instructions|rules)", re.I
        ),
        0.8,
        "override_instructions",
    ),
    (
        re.compile(r"\b(forget|drop) (everything|all|your) (you were told|instructions|rules)", re.I),
        0.7,
        "override_instructions",
    ),
    (
        re.compile(r"\byou are now\b|\bfrom now on you (are|will)\b|\bnew persona\b", re.I),
        0.5,
        "persona_switch",
    ),
    (re.compile(r"\b(developer|god|admin|sudo|jailbreak|dan) mode\b", re.I), 0.7, "jailbreak"),
    (re.compile(r"\bact as (an? )?(unrestricted|unfiltered|uncensored|evil)", re.I), 0.7, "jailbreak"),
    (
        re.compile(
            r"\b(unrestricted|unfiltered|uncensored) (ai|assistant|model|mode|version)\b|\bwith no (rules|restrictions|limits|filters)\b",
            re.I,
        ),
        0.5,
        "jailbreak",
    ),
    (
        re.compile(
            r"(reveal|print|show|repeat|output|leak|display|tell me) (me )?(your|the|all) (full |hidden |initial |original |secret |internal )?(system )?(prompt|instructions|rules|configuration)",
            re.I,
        ),
        0.8,
        "prompt_leak",
    ),
    (re.compile(r"\bsystem prompt\b", re.I), 0.4, "prompt_leak"),
    (
        re.compile(r"<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]|<<SYS>>|### (system|instruction)", re.I),
        0.9,
        "template_tokens",
    ),
    (
        re.compile(r"\b(begin|end) of (system|assistant|hidden) (message|instructions)\b", re.I),
        0.7,
        "template_tokens",
    ),
    (
        re.compile(
            r"\bas (the|an?) (administrator|developer|operator|owner) (i|we) (authori[sz]e|order|command|instruct)",
            re.I,
        ),
        0.7,
        "authority_claim",
    ),
    (
        re.compile(
            r"\bdo not (tell|inform|mention to) the (user|customer)\b|\bsecretly\b|\bwithout (the user|them) knowing\b",
            re.I,
        ),
        0.6,
        "covert_instruction",
    ),
    (re.compile(r"!\[[^\]]*\]\(https?://[^)]*[?&][^)]*\)", re.I), 0.9, "markdown_exfiltration"),
    (
        re.compile(
            r"\b(send|post|forward|email) (this|the|all|every) (conversation|transcript|data|secrets?|keys?) to\b",
            re.I,
        ),
        0.7,
        "exfiltration",
    ),
    (
        re.compile(
            r"\b(api[_ ]?key|password|secret key|credentials?)\b.*\b(reveal|show|print|give|tell)\b|\b(reveal|show|print|give|tell)\b.*\b(api[_ ]?key|password|secret key|credentials?)\b",
            re.I,
        ),
        0.6,
        "secret_request",
    ),
    (
        re.compile(
            r"\b(every|all|other) customers?(?:'s|s'|')? (orders?|emails?|email addresses|data|details|accounts?)\b",
            re.I,
        ),
        0.5,
        "cross_customer_data",
    ),
    (re.compile(r"[A-Za-z0-9+/]{120,}={0,2}"), 0.4, "encoded_blob"),
    (re.compile(r"\b(assistant|ai|model)\s*:\s*(sure|ok|certainly)", re.I), 0.5, "fake_turn"),
]


@dataclass(slots=True)
class HeuristicVerdict:
    score: float
    matched: list[str] = field(default_factory=list)

    @property
    def category(self) -> str | None:
        return self.matched[0] if self.matched else None


def scan_text(text: str) -> HeuristicVerdict:
    score = 0.0
    matched: list[str] = []
    for pattern, weight, label in _RULES:
        if pattern.search(text):
            score += weight
            if label not in matched:
                matched.append(label)
    return HeuristicVerdict(score=min(1.0, round(score, 3)), matched=matched)
