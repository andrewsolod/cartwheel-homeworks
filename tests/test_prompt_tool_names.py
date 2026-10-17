"""The system prompt names tools explicitly ("call search_help_center ...").
A renamed tool would leave a stale name in the prompt and silently stop the
model from following the instruction, so fail loudly instead (no LLM)."""

from __future__ import annotations

import re

from agent.agent import SYSTEM_PROMPT_TEMPLATE, TOOLS_BY_ROLE


def test_every_tool_named_in_the_prompt_exists() -> None:
    registered = {tool.name for tools in TOOLS_BY_ROLE.values() for tool in tools}
    # Tool names are snake_case identifiers with at least one underscore;
    # {user_id}-style template fields are session context, not tools.
    text = re.sub(r"\{\w+\}", "", SYSTEM_PROMPT_TEMPLATE)
    named = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text))
    assert named, "expected the prompt to name at least one tool"
    assert named <= registered, f"prompt names unknown tools: {sorted(named - registered)}"
