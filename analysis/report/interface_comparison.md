# Interface comparison

My review interface is in `analysis/review_app/` (`uv run python -m analysis.review_app.server`, port 8021). The reference is `analysis/server.py` with `analysis/ui/index.html`. I reviewed live traces from Langfuse; the Module 1 export (`traces/support_traces.json`) is only an offline fallback (`--source`) and was not needed.

## Kept from the reference interface

A tool call and its result share one container, placed between the agent text before it and the reply after it. Many Cartwheel failures sit in the gap between what a tool returned and what the reply says it returned, so the two need to be read side by side. I also kept the reference's file-backed API over `analysis/state/` and its free-text margin notes next to the highlighted text.

## Changed after inspecting the traces

The unit of review is the whole conversation, not one trace. In the standard Langfuse view I had to open each trace and dig through the observation tree to find the user message, the tool calls and the reply. Each user turn is a separate trace, and Langfuse's `sessionId` is empty on these traces, so a follow-up turn appeared without the turn before it. The interface groups the traces by `cartwheel.scenario_id`, shows the turns in order, and joins each conversation with the context Langfuse does not have:

- the simulated user's instructions (the writer brief), to tell a bad generated message from a bad agent reply;
- the expected outcome, visible but collapsible;
- the scenario tuple and the database record the scenario is about;
- the agent's system prompt (one template; only the session lines differ by user).

Notes can be attached to any of this text, not only to messages and tool results. Two smaller changes: a rejected AI suggestion stays in `suggestions.json` with its reason instead of being deleted, and binary labels have their own grid (conversation × mode) that writes `labels/<mode>.jsonl` and a Langfuse score on the conversation id.

## Remaining limitation

The database panel shows the record from a freshly seeded copy, not the record as the agent saw it. The final run changed some orders (refunds, cancellations), and earlier runs had changed others, so for those records the panel can differ from what the tool returned during the conversation. The tool result in the conversation is the authoritative evidence; the panel is context only.
