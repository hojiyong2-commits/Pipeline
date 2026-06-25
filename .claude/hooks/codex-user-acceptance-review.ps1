# Claude Code Stop hook 진입점. CLAUDE_HOOK_TRANSCRIPT_PATH 환경변수에서
# transcript 경로를 읽어 Python helper에 전달하는 thin wrapper.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$helperPath = Join-Path $scriptDir "codex_user_acceptance_review.py"
$transcriptPath = $env:CLAUDE_HOOK_TRANSCRIPT_PATH

if ($transcriptPath -and (Test-Path $transcriptPath)) {
    python $helperPath --transcript $transcriptPath
} else {
    python $helperPath
}
exit $LASTEXITCODE
