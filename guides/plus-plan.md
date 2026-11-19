# Plus plan — Codex 6.x

The Plus profile uses GPT-6.2 Sol high as root, GPT-6 Luna for implementation,
GPT-6.2 Sol for research, GPT-6.2 Astra for independent review and GPT-6.1 Terra
for visual QA. Runtime access to individual models depends on the user's Codex
login and rollout status.

| Role | Model | Reasoning | Access |
|---|---|---|---|
| Root / orchestrator | gpt-6.2-sol | high | workspace-write |
| Explorer | gpt-6-luna | medium | read-only |
| Worker | gpt-6-luna | medium | workspace-write |
| Tester | gpt-6-luna | high | workspace-write |
| Researcher | gpt-6.2-sol | medium | read-only |
| Reviewer | gpt-6.2-astra | medium | read-only |
| Visual-inspector | gpt-6.1-terra | medium | read-only |

The six named roles are declared in `profiles/plus/codex/config.toml` with
relative `config_file` paths and pinned models in separate agent files. The
default generic spawned agent uses Luna medium. The configured concurrency
limit is four.

Use `setup.ps1` on Windows, select plan **2 (Plus)** and review the target
paths. Existing files require an explicit overwrite decision and backups are
created before replacement. Existing global MCP configuration is not replaced.
For global installation, merge the project settings into
`~/.codex/config.toml` instead of overwriting it, and copy the named role
files into `~/.codex/agents/`.

Keep `approval_policy = "on-request"` and `sandbox_mode = "workspace-write"`.
Read-only role instructions restrict intended behavior. For enforced read-only access,
run a separate read-only Codex session and disable write-capable MCP tools. Merge, deployment, deletion and changes to permissions always
require explicit authorization.

Use `codex --version` and the interactive `/model` picker to validate access
before using the profile. GPT-6.2 Sol and Astra require Codex CLI >= 0.166.0. For usage measurement, see `token-usage.md`.
