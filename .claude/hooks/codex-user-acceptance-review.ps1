# [Purpose]: Claude Code Stop hook의 PowerShell 진입점. Python helper
#   codex_user_acceptance_review.py를 호출하고 transcript 경로를 전달하는 thin wrapper.
# [Assumptions]: python이 PATH에 있고, 같은 디렉토리에 codex_user_acceptance_review.py가 존재한다.
# [Vulnerability & Risks]: python 미설치 시 호출이 실패한다 — helper가 fail-closed로 처리하며,
#   wrapper는 helper 종료 코드를 그대로 전파하므로 추가 위험은 없다.
# [Improvement]: 시간이 더 있다면 python 실행기 후보(py, python3)를 순차 탐색할 것이다.
param([string]$TranscriptPath = "")

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$helperPath = Join-Path $scriptDir "codex_user_acceptance_review.py"

python $helperPath --transcript $TranscriptPath
exit $LASTEXITCODE
