# Codex project instructions

Follow explicit user instructions and existing repository security policies. Never merge, deploy, publish, delete data, weaken permissions, or change secrets without explicit authorization for that action.

Choose orchestration depth by task:
- **Quick:** answer directly or make one localized edit; no compulsory subagents.
- **Standard:** use the smallest useful set of explorer, worker, researcher, or tester for scoped multi-file work.
- **Full:** for high-risk or independent workstreams, plan ownership, delegate bounded tasks, validate changes, and request independent reviewer/visual-inspector findings as relevant.

Root owns design choices, integration and final verification. Assign one writer per file, limit concurrent agents to the configured cap, and never treat another agent's report as test evidence. Read-only agents must not modify files. Keep user-facing reports short, state which tests actually ran, and mark unsupported claims NOT CHECKED.

When tools or roles are unavailable, continue safely with available tools and disclose the limitation; do not pretend delegation or checks happened.
