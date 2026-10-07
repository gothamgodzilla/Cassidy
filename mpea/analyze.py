# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Step 1 of the MPEA workflow: goal, model, and draft deficiencies."""

import re
from dataclasses import dataclass, field

from mpea.profiles import ModelProfile, resolve_profile


_INJECTION = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(your|the|all)\s+(rules|instructions|guidelines)", re.I),
    re.compile(r"reveal\s+(your\s+)?(system\s+prompt|hidden\s+instructions|secrets?)", re.I),
    re.compile(r"you\s+are\s+now\b", re.I),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"do\s+anything\s+now", re.I),
    re.compile(r"</?(system|instructions)\b", re.I),
)

_VAGUE = re.compile(
    r"\b(stuff|things|whatever|and so on|make it better|optimize this|help me out)\b",
    re.I,
)

# Objectives the assembler will not turn into a deployable prompt.
_DISALLOWED = (
    (re.compile(r"\bransomware\b", re.I), "ransomware"),
    (re.compile(r"\bphishing\b", re.I), "phishing"),
    (re.compile(r"\b(keylogger|credential[-\s]?stealer)\b", re.I), "credential-theft malware"),
    (
        re.compile(
            r"\b(steal|exfiltrate|harvest)\b.{0,48}\b(passwords?|credentials?|api keys?|secret tokens?)\b",
            re.I,
        ),
        "credential theft",
    ),
    (
        re.compile(r"\b(write|create|build|generate)\b.{0,48}\b(malware|shellcode|exploit payload)\b", re.I),
        "offensive tooling",
    ),
)


@dataclass
class Analysis:
    """Findings from the draft. Injection text is recorded and kept out of instructions."""

    goal: str
    audience: str
    domain: str
    output_format: str
    constraints: tuple[str, ...]
    examples: tuple[tuple[str, str], ...]
    profile: ModelProfile
    unknown_model: bool
    deficiencies: list[str] = field(default_factory=list)
    injection_findings: list[str] = field(default_factory=list)
    sanitized_source: str = ""
    refusal_reason: str = ""

    @property
    def refused(self) -> bool:
        return bool(self.refusal_reason)


def _line_injection(line: str) -> str:
    for pattern in _INJECTION:
        if pattern.search(line):
            return pattern.pattern
    return ""


def _disallowed_reason(text: str) -> str:
    for pattern, label in _DISALLOWED:
        if pattern.search(text):
            return label
    return ""


def _sanitize(draft: str) -> tuple[str, list[str]]:
    findings = []
    kept = []
    for line in (draft or "").splitlines():
        if _line_injection(line):
            findings.append(line.strip()[:180])
            continue
        kept.append(line)
    return "\n".join(kept).strip(), findings


def analyze(
    goal: str = "",
    draft: str = "",
    target_llm: str = "",
    audience: str = "",
    domain: str = "",
    output_format: str = "",
    constraints: tuple[str, ...] | list[str] | None = None,
    examples: tuple[tuple[str, str], ...] | list[tuple[str, str]] | None = None,
) -> Analysis:
    """Identify the goal, the target model, and what the draft leaves unspecified."""

    profile, unknown = resolve_profile(target_llm)
    sanitized, injections = _sanitize(draft)
    clean_goal = (goal or "").strip()
    if not clean_goal:
        clean_goal = sanitized.split("\n\n", 1)[0].strip()[:400]

    combined = "\n".join(part for part in (goal, draft) if part)
    reason = _disallowed_reason(combined)

    deficiencies = []
    if not (goal or "").strip() and not sanitized:
        deficiencies.append("No goal and no usable source text. The objective is unspecified.")
    elif not (goal or "").strip():
        deficiencies.append("Goal was inferred from the draft. Confirm it before deployment.")
    if unknown:
        deficiencies.append(
            "Target model is missing or not in the catalog. A generic, self-contained structure was used."
        )
    if _VAGUE.search(combined or ""):
        deficiencies.append("Vague wording (for example 'optimize this' or 'things') with no success check.")
    if not (audience or "").strip():
        deficiencies.append("Audience is missing, so tone and depth have no anchor.")
    if not (output_format or "").strip():
        deficiencies.append("Output format was not specified. A fixed markdown contract was applied.")
    if injections:
        deficiencies.append(
            "Prompt-injection text was found in the draft and removed from the instruction channel."
        )
    if not examples:
        deficiencies.append("No few-shot pair was supplied. The prompt forbids invented domain facts.")

    return Analysis(
        goal=clean_goal,
        audience=(audience or "").strip(),
        domain=(domain or "").strip(),
        output_format=(output_format or "").strip(),
        constraints=tuple(item.strip() for item in (constraints or []) if item and item.strip()),
        examples=tuple(examples or ()),
        profile=profile,
        unknown_model=unknown,
        deficiencies=deficiencies,
        injection_findings=injections,
        sanitized_source=sanitized,
        refusal_reason=reason,
    )
