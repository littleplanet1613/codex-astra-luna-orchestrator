# Plus profile upgrade — 2026-11-09

| Role | Model | Effort | Sandbox |
|---|---|---|---|
| root | gpt-6.1-sol | high | workspace-write |
| explorer | gpt-6-luna | medium | read-only |
| researcher | gpt-6.1-sol | medium | read-only |
| worker | gpt-6-luna | medium | workspace-write |
| tester | gpt-6-luna | high | workspace-write |
| reviewer | gpt-6-astra | medium | read-only |
| visual-inspector | gpt-6.1-terra | medium | read-only |

The six named agents are explorer, researcher, worker, tester, reviewer, and visual-inspector.
Use only the models available to the signed-in Codex account. GPT-6 Astra requires Codex CLI >= 0.153.0 and GPT-6.1 Terra requires >= 0.159.0 per OpenAI's current documentation; the installer warns rather than upgrading software. Run `codex --version`, then inspect the available models from Codex. Model names in TOML are requested identifiers, not proof of entitlement.

Security preserved: `approval_policy = "on-request"`, `sandbox_mode = "workspace-write"`, concurrency cap 4, read-only review/research/exploration. No automatic merge, deployment or permission changes.

Windows installer: it warns about existing files, requires explicit consent to overwrite, saves a timestamped backup in the target repo before replacing approved components, and preserves unrelated MCP/global settings. For rollback, restore from the backup directory. To validate PowerShell syntax locally on Windows: `$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\setup.ps1),[ref]$tokens,[ref]$errors); $errors`. To validate models, run Codex CLI interactively with the selected profile; a static TOML parse cannot prove model access.

This PR deliberately does not change the Pro profile's five pre-existing role pins. Pro receives the streamlined orchestration skill; only Plus adds the visual-inspector.

## Explicit role registration

Each Plus role is declared in `.codex/config.toml` as `[agents."<role>"]` with a relative `config_file = "agents/<role>.toml"`. This is necessary for reliable custom-role discovery in Codex. The role-specific file controls the model and sandbox mode. Verify actual role spawning when Codex CLI is available on Windows.
