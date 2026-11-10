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

"""Target-model profiles used to shape an engineered prompt.

Notes are prompt-design guidance (strengths, failure modes, and a section
style). They are not a claim that any model is universally better.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    """Architectural tendencies that the prompt structure should compensate for."""

    canonical_id: str
    display_name: str
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    # markdown: heading sections. xml: tagged sections. delimited: explicit fences.
    section_style: str
    style_note: str


_PROFILES = (
    ModelProfile(
        canonical_id="gpt-4o",
        display_name="GPT-4o",
        strengths=(
            "Strong instruction following and schema adherence.",
            "Reliable JSON when the schema is shown, not merely named.",
            "Good at short, tool-shaped tasks with explicit success checks.",
        ),
        weaknesses=(
            "Drifts when the output schema is described in prose only.",
            "May add helpful preamble unless told the response starts at the artifact.",
        ),
        section_style="markdown",
        style_note="Use markdown headings and, when the output is structured, paste the schema in the output section.",
    ),
    ModelProfile(
        canonical_id="claude-3.5-sonnet",
        display_name="Claude 3.5 Sonnet",
        strengths=(
            "Careful with long context and explicit negative constraints.",
            "Follows XML section boundaries reliably.",
            "States uncertainty when the prompt allows it.",
        ),
        weaknesses=(
            "Over-explains when the length budget is missing.",
            "May treat a polite request to ignore rules as a new user preference unless untrusted text is fenced.",
        ),
        section_style="xml",
        style_note="Wrap every C.O me.R.E. section in XML tags and set a hard length budget.",
    ),
    ModelProfile(
        canonical_id="gemini-1.5-pro",
        display_name="Gemini 1.5 Pro",
        strengths=(
            "Large context and multimodal inputs when the prompt names the modality.",
            "Useful for broad synthesis across a supplied corpus.",
        ),
        weaknesses=(
            "Looser adherence when constraints are buried in a long preamble.",
            "May answer from general knowledge when the prompt does not say the supplied material is the only source.",
        ),
        section_style="markdown",
        style_note="Put constraints before the task body and state that supplied material is the only source of facts.",
    ),
    ModelProfile(
        canonical_id="llama-3",
        display_name="Llama 3",
        strengths=(
            "Predictable when delimiters and the output schema are repeated.",
            "Works well with short imperative sentences.",
        ),
        weaknesses=(
            "Ambiguous pronouns and implied formats cause format drift.",
            "Smaller-context variants drop instructions placed only at the top.",
        ),
        section_style="delimited",
        style_note="Use explicit section fences and repeat the output contract at the end of the prompt.",
    ),
    ModelProfile(
        canonical_id="deepseek",
        display_name="DeepSeek",
        strengths=(
            "Strong on code, math, and stepwise technical reasoning.",
            "Stays terse when brevity is required.",
        ),
        weaknesses=(
            "Will skip user-facing explanation unless the output section demands it.",
            "May assume a coding task when the domain is unspecified.",
        ),
        section_style="markdown",
        style_note="State the domain in the role section and require a non-code explanation when the task is not code.",
    ),
)

_ALIASES = {
    "gpt-4o": "gpt-4o",
    "gpt4o": "gpt-4o",
    "gpt-4": "gpt-4o",
    "claude-3.5-sonnet": "claude-3.5-sonnet",
    "claude-3-5-sonnet": "claude-3.5-sonnet",
    "claude": "claude-3.5-sonnet",
    "gemini-1.5-pro": "gemini-1.5-pro",
    "gemini-1-5-pro": "gemini-1.5-pro",
    "gemini": "gemini-1.5-pro",
    "llama-3": "llama-3",
    "llama3": "llama-3",
    "llama-3.1": "llama-3",
    "deepseek": "deepseek",
    "deepseek-v3": "deepseek",
    "deepseek-chat": "deepseek",
}

_GENERIC = ModelProfile(
    canonical_id="generic",
    display_name="unspecified model",
    strengths=("No model-specific strength is assumed.",),
    weaknesses=(
        "Unknown instruction-following limits, so the prompt must be self-contained.",
    ),
    section_style="markdown",
    style_note="Use plain markdown sections and do not depend on vendor JSON modes or tool wrappers.",
)

_BY_ID = {profile.canonical_id: profile for profile in _PROFILES}


def resolve_profile(target: str) -> tuple[ModelProfile, bool]:
    """Return the profile and whether the name was unknown."""

    key = re.sub(r"[\s_]+", "-", (target or "").strip().lower())
    if not key:
        return _GENERIC, True
    canonical = _ALIASES.get(key)
    if canonical is None:
        return _GENERIC, True
    return _BY_ID[canonical], False
