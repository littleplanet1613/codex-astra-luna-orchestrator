$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$setup = Join-Path $repoRoot 'setup.ps1'
$target = Join-Path ([IO.Path]::GetTempPath()) ('codex-installer-smoke-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $target | Out-Null
try {
    $inputLines = @($target, '2', 'y', 'y', 'y') -join [Environment]::NewLine
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = (Get-Command pwsh).Source
    $psi.ArgumentList.Add('-NoProfile')
    $psi.ArgumentList.Add('-File')
    $psi.ArgumentList.Add($setup)
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $process = [Diagnostics.Process]::Start($psi)
    $process.StandardInput.WriteLine($inputLines)
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit(30000) | Out-Null
    if ($process.ExitCode -ne 0) { throw "Installer failed ($($process.ExitCode)): $stdout $stderr" }

    $config = Join-Path $target '.codex/config.toml'
    $visual = Join-Path $target '.codex/agents/visual-inspector.toml'
    $skill = Join-Path $target '.agents/skills/astra-orchestrator/SKILL.md'
    $instructions = Join-Path $target 'AGENTS.md'
    foreach ($file in @($config, $visual, $skill, $instructions)) {
        if (-not (Test-Path -LiteralPath $file)) { throw "Missing installed file: $file" }
    }
    if (-not ((Get-Content $config -Raw).Contains('gpt-6.1-sol'))) { throw 'Wrong Plus profile installed' }
    if (-not ((Get-Content $visual -Raw).Contains('gpt-6.1-terra'))) { throw 'visual-inspector model not installed' }

    # Re-run and refuse every existing component; existing content must stay intact.
    $before = (Get-FileHash -Algorithm SHA256 $config).Hash
    $inputLines = @($target, '2', 'y', 'n', 'y', 'n', 'y') -join [Environment]::NewLine
    $psi2 = [Diagnostics.ProcessStartInfo]::new()
    $psi2.FileName = (Get-Command pwsh).Source
    foreach ($arg in @('-NoProfile', '-File', $setup)) { $psi2.ArgumentList.Add($arg) }
    $psi2.RedirectStandardInput = $true
    $psi2.RedirectStandardOutput = $true
    $psi2.RedirectStandardError = $true
    $psi2.UseShellExecute = $false
    $process2 = [Diagnostics.Process]::Start($psi2)
    $process2.StandardInput.WriteLine($inputLines)
    $process2.StandardInput.Close()
    $output2 = $process2.StandardOutput.ReadToEnd()
    $error2 = $process2.StandardError.ReadToEnd()
    $process2.WaitForExit(30000) | Out-Null
    if ($process2.ExitCode -ne 0) { throw "Rerun failed ($($process2.ExitCode)): $output2 $error2" }
    if ($before -ne (Get-FileHash -Algorithm SHA256 $config).Hash) { throw 'Config changed despite refusing overwrite' }
    Write-Output 'Windows installer smoke: PASS'
}
finally {
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
}
