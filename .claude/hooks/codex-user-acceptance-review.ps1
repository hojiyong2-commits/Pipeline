# Claude Code Stop hook 진입점.
# Claude Code는 hook 데이터를 stdin JSON으로 전달합니다.
# {"hook_event_name":"Stop","last_assistant_message":"...","stop_hook_active":false}
# raw bytes passthrough: PowerShell string 변환 없이 stdin을 Python helper에게 직접 전달합니다.
# (IMP-20260627-3907: $input|python 파이프는 Korean 등 non-ASCII를 ?로 변환하므로 교체)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$helperPath = Join-Path $scriptDir "codex_user_acceptance_review.py"
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Get-Command python).Source
$psi.Arguments = "`"$helperPath`""
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$p = [System.Diagnostics.Process]::Start($psi)
try {
    [System.Console]::OpenStandardInput().CopyTo($p.StandardInput.BaseStream)
} finally {
    $p.StandardInput.Close()
}
$p.WaitForExit()
exit $p.ExitCode
