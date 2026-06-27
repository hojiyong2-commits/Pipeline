# Claude Code Stop hook 진입점.
# Claude Code는 hook 데이터를 stdin JSON으로 전달합니다.
# {"hook_event_name":"Stop","last_assistant_message":"...","stop_hook_active":false}
# stdin을 Python helper에게 pipe로 그대로 전달합니다.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$helperPath = Join-Path $scriptDir "codex_user_acceptance_review.py"
$input | python $helperPath
exit $LASTEXITCODE
