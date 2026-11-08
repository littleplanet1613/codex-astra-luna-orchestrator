---
name: astra-orchestrator
description: Coordinate non-trivial Codex repository work using scoped subagents, independent verification, and three levels of effort. Use for multi-file changes, high-risk work, or explicitly requested delegation; skip for trivial work.
---

# Codex Orchestration — PRO profile

The user's instructions and existing approval/sandbox/security constraints take precedence.

## Select a mode
- **Quick:** direct, localized changes or answers. No delegation requirement.
- **Standard:** use only the roles that add value to multi-file work; define ownership and acceptance checks.
- **Full:** high-risk or independently parallel workstreams. Explore, implement, test, review, and inspect visuals if needed.

## Roles and model source
Use the installed `.codex/agents/*.toml` role definitions as the source of truth for model and reasoning effort; do not hardcode model IDs into spawn prompts. On Plus, the root is Sol, with Luna execution, Sol research, Astra review, and Terra visual inspection. On Pro, retain the existing Astra root and current pinned roles unless explicitly changed. Role availability and model access must be checked in the installed Codex CLI.

- explorer: read-only repository mapping
- researcher: read-only, versioned evidence from primary documentation
- worker: bounded code changes, one owner per file
- tester: targeted verification; changes only when asked
- reviewer: independent read-only defect/security review
- visual-inspector: read-only screenshot/render QA, only when visual evidence exists

## Working rules
1. Keep changes within the user's scope. Never merge, deploy, erase data, expose secrets or relax permissions without explicit approval.
2. Spawn roles only when the tool is available and the task benefits; never claim a role ran unless it did.
3. Assign objective, exact file scope, constraints, deliverable and acceptance criteria.
4. Parallelize only independent work and avoid overlapping edits. Respect the configured concurrency limit.
5. Verify actual diff and run targeted checks after the final change. Reviewer reports do not replace tests.
6. State test counts, findings, and anything NOT CHECKED. If a model or tool is unavailable, report the limitation and use an authorized fallback, not a silent substitute.
