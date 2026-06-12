"""
conftest.py
-----------
pytest 설정 — ic_part_src/ 와 프로젝트 루트를 sys.path에 추가합니다.
ic_part_src: order_mapper, automation import용
프로젝트 루트: core.acceptance, pipeline 등 프로젝트 패키지 import용

IMP-20260611-A716 MT-5: PIPELINE_GH_EXECUTABLE fixture.
PR body readiness 검사(Bug 1 수정)로 인해 gh CLI 없는 환경에서 request-accept가 BLOCKED됨.
기존 테스트들은 gh CLI 없는 환경에서도 request-accept가 성공하길 기대하므로,
autouse fixture로 완전한 PR body를 반환하는 fake gh를 설정한다.
"""
import json
import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_IC_PART_SRC = _PROJECT_ROOT / "ic_part_src"

# 프로젝트 루트를 sys.path에 추가 (core.acceptance 등 import용)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ic_part_src 디렉토리를 sys.path 최우선 위치에 삽입
if str(_IC_PART_SRC) not in sys.path:
    sys.path.insert(0, str(_IC_PART_SRC))


# ---------------------------------------------------------------------------
# IMP-20260611-A716 MT-5: fake gh fixture
# Bug 1 수정(request-accept에서 PR body None → BLOCKED) 으로 인한 회귀를 방지.
# 기존 테스트들이 request-accept 성공을 기대할 때 PR body를 제공하기 위해
# PIPELINE_GH_EXECUTABLE로 완전한 PR body를 반환하는 fake gh_spy.py를 설정한다.
# 단, PIPELINE_GH_EXECUTABLE이 이미 설정되어 있으면 override하지 않는다.
# ---------------------------------------------------------------------------

_COMPLETE_PR_BODY = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)

_FAKE_GH_SPY_TEMPLATE = '''\
import sys
import io
import json

# IMP-20260611-A716: Windows 콘솔 코드 페이지(cp949)가 UTF-8 한국어를 깨뜨리는 문제 방지.
# sys.stdout을 UTF-8 TextIOWrapper로 교체하여 항상 UTF-8로 출력한다.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BODY = {body_json}
EXIT_CODE = 0
args = sys.argv[1:]

if EXIT_CODE != 0:
    sys.exit(EXIT_CODE)

# gh pr view --json ... --jq .body
if '--jq' in args:
    jq_idx = args.index('--jq')
    jq_expr = args[jq_idx + 1] if jq_idx + 1 < len(args) else ''
    if jq_expr == '.body':
        sys.stdout.write(BODY)
        if not BODY.endswith('\\n'):
            sys.stdout.write('\\n')
        sys.exit(0)
    elif '[.files' in jq_expr or jq_expr.startswith('.[0]'):
        print('[]')
        sys.exit(0)
    elif '.headSha' in jq_expr or '.databaseId' in jq_expr:
        print('')
        sys.exit(0)

if 'run' in args and 'list' in args:
    print('[]')
    sys.exit(0)

if 'run' in args and 'view' in args:
    print(json.dumps({{}}))
    sys.exit(0)

if 'pr' in args and 'list' in args:
    print('[]')
    sys.exit(0)

result = {{
    'body': BODY,
    'number': 1,
    'headRefOid': 'abc123def456abc123def456abc123def456abc1',
    'isDraft': False,
    'state': 'OPEN',
    'files': [],
    'url': 'https://github.com/test/repo/pull/1',
}}
print(json.dumps(result))
sys.exit(0)
'''


@pytest.fixture(autouse=True)
def _default_fake_gh_for_pr_body(monkeypatch, tmp_path):
    """IMP-20260611-A716 MT-5: 모든 테스트에 완전한 PR body를 반환하는 fake gh를 설정.

    PIPELINE_GH_EXECUTABLE이 이미 설정된 경우(테스트가 직접 mock을 지정한 경우) override 안 함.
    테스트가 pr_body_not_found BLOCKED를 기대하면 PIPELINE_GH_EXECUTABLE을 None으로 unset할 것.
    """
    if os.environ.get("PIPELINE_GH_EXECUTABLE"):
        # 이미 설정되어 있으면 skip (테스트가 직접 fake gh를 지정한 경우)
        yield
        return

    # fake gh_spy.py 생성
    spy_content = _FAKE_GH_SPY_TEMPLATE.format(
        body_json=json.dumps(_COMPLETE_PR_BODY)
    )
    spy_path = tmp_path / "conftest_gh_spy.py"
    spy_path.write_text(spy_content, encoding="utf-8")

    # PIPELINE_GH_EXECUTABLE 설정
    monkeypatch.setenv("PIPELINE_GH_EXECUTABLE", str(spy_path))
    yield
