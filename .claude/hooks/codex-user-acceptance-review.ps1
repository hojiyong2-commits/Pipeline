# Claude Code Stop hook 진입점.
# Claude Code는 transcript 경로를 stdin JSON { "transcript_path": "..." } 으로 전달합니다.
# stdin을 읽어 transcript_path를 추출 후 Python helper에 --transcript와 --stdin-json으로 전달합니다.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$helperPath = Join-Path $scriptDir "codex_user_acceptance_review.py"

# stdin에서 JSON 읽기 (Claude Code Stop hook 전달 방식)
$stdinContent = $input | Out-String
$transcriptPath = $null
if ($stdinContent.Trim()) {
    try {
        $hookData = $stdinContent | ConvertFrom-Json
        $transcriptPath = $hookData.transcript_path
    } catch {}
}

if ($transcriptPath -and (Test-Path $transcriptPath)) {
    python $helperPath --transcript $transcriptPath --stdin-json $stdinContent
} else {
    python $helperPath --stdin-json $stdinContent
}
exit $LASTEXITCODE
