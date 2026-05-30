<!-- v1 안정화 릴리즈: IMP-20260529-D5DC -->
# Pipeline v1 안정화 릴리즈 가이드

> **v1.0 기준선 — 실제 업무 사용 가능 (2026-05-30)**
> 이 파이프라인은 Security & Secrets Boundary(IMP-20260529-D8BA)까지 머지된 상태로 v1 기준선이 고정됩니다.

## 빠른 시작

```powershell
# 새 파이프라인 시작
python pipeline.py new --type FEAT|BUG|IMP --desc "작업 설명"
# 현재 상태 확인
python pipeline.py status
# 전체 게이트 상태 확인
python pipeline.py gates status
```

## 포함된 핵심 게이트 (v1.0)

| 게이트 | 명령어 | 도입 IMP |
|---|---|---|
| Security & Secrets Boundary | `python pipeline.py gates secrets` | IMP-20260529-D8BA |
| Golden Task | `python pipeline.py golden run --smoke` | IMP-20260528-0A9E |
| Workspace Hygiene | `python pipeline.py gates preflight-pr-impl` | IMP-20260528-3898 |
| Cost/Attempt Budget | `python pipeline.py budget status` | IMP-20260527-075A |
| Observability Metrics | `python pipeline.py metrics report` | IMP-20260526-82E3 |
| Oracle Quality Gate | `python pipeline.py gates oracle` | IMP-20260524-48C4 |
| Real CLI Path E2E | `pytest tests/e2e/ -v` | IMP-20260525-6FAC |
| Phase Attestation (Option A) | `python pipeline.py gates phase-ci --phase pm` | IMP-20260516-D069 |

## ACCEPT / REJECT 판단 기준

사용자는 **코드가 아니라 결과물**을 보고 판단합니다.

**ACCEPT 조건 (모두 충족 시):**
- PR 링크와 GitHub Actions CI 결과가 제공됨
- 요청한 기능이 결과물(파일, EXE, 문서, 출력)에 실제로 반영됨
- GitHub Actions CI가 PASS 상태
- 추측 문구("아마도", "확인 필요", "예상") 없음

**REJECT 조건 (하나라도 해당 시):**
- 요청한 기능이 빠져 있음
- CI가 FAIL 상태
- 결과물 경로/링크가 제공되지 않음
- 실제 동작이 요청과 다름

## 실패 시 대응 절차

| 상황 | 명령어 |
|---|---|
| 어느 phase가 막혔는지 확인 | `python pipeline.py status` |
| Technical gate 실패 | `python pipeline.py gates technical` → 오류 메시지 확인 |
| Oracle gate 실패 | `python pipeline.py gates oracle` → 기대값 vs 실제값 비교 |
| CI 실패 | GitHub Actions 로그 확인 → Dev 재작업 |
| Secrets gate 실패 | `python pipeline.py gates secrets` → 마스킹된 파일 경로 확인 후 제거 |
| QA 동일 실패 2회 반복 | Circuit Breaker 발동 → `python pipeline.py status` 확인 |

## 현재 알려진 제한사항 (v1.0)

1. **Codex Review (GPT-5.5)**: OpenAI API 쿼터 소진 시 `review codex-record`로 수동 기록 필요
2. **Google Drive 배포**: `G:\내 드라이브\터미널\` 경로가 마운트되지 않으면 배포 단계 SKIP
3. **Windows 전용**: `pipeline.py`는 Windows PowerShell 환경에서 개발됨. Linux/macOS는 경로 구분자 조정 필요
4. **Phase attestation**: GitHub Actions가 실행되지 않는 환경(오프라인)에서는 phase-ci gate 수동 처리 필요
5. **git tag**: v1.0 태그는 자동 생성되지 않음 — PR #356 merge 후 main 최신화 → `git pull origin main && git tag v1.0 && git push origin v1.0`

---

(기존 README.md 내용은 이 주석 줄 바로 다음부터 계속 이어집니다)

# Pipeline — 자체 개선형 다중 에이전트 파이프라인

작업 순서, 외부 검증, 사용자 최종 승인을 강제하는 파이프라인입니다.

## 현재 기본 구조

모든 파이프라인은 이제 예외 없이 Three-Gate + Option A + Incremental Module Gate로 실행합니다. 이 셋은 선택 모드가 아닙니다.

쉽게 말하면:

1. PM이 요구사항을 작은 `MT-N` 단위로 쪼갭니다.
2. 각 `MT-N`은 `module design -> module dev -> module qa`를 통과해야 다음 모듈로 넘어갑니다.
3. 모든 모듈이 PASS되면 `module integrate`로 합칩니다.
4. PM/Dev/QA/Build phase는 각각 GitHub Actions phase attestation을 통과해야 다음 역할 경계로 넘어갑니다.
5. 최종 완료는 Technical, Oracle, GitHub CI, User Acceptance가 모두 PASS된 뒤에만 가능합니다.

사용자가 마지막에 해야 할 일은 코드 리뷰가 아니라 결과물 확인입니다. 마지막 작업 담당자가 PR 링크와 GitHub Actions의 한국어 **최종 확인 안내**, 실제 결과물 경로나 첨부파일 링크를 줍니다. 사용자는 그 결과물이 요청과 맞는지만 보고 승인(ACCEPT) 또는 거절(REJECT)을 선택합니다.

## 필수 명령 흐름

```powershell
python pipeline.py contract init
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <pm_run_id>
python pipeline.py gates prepare-phase --phase pm
python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline

python pipeline.py module design --mt-id MT-1 --report-file module_design_MT-1.xml
python pipeline.py module dev --mt-id MT-1 --files "path/to/file.py" --report-file module_handover_MT-1.xml --scope-manifest scope_manifest_MT-1.json
python pipeline.py module qa --mt-id MT-1 --result PASS --report-file module_qa_MT-1.xml

# 모든 MT-N에 module design/dev/qa를 반복한 뒤:
python pipeline.py module integrate --result PASS --report-file integration_report.xml

python pipeline.py done --phase dev --files "path/to/file.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>
python pipeline.py gates prepare-phase --phase dev
python pipeline.py gates phase-ci --phase dev --repo hojiyong2-commits/Pipeline

python pipeline.py qa --result PASS --numeric-score 110 --report-file qa_report.xml --agent-run-id <qa_run_id>
python pipeline.py gates prepare-phase --phase qa
python pipeline.py gates phase-ci --phase qa --repo hojiyong2-commits/Pipeline

python pipeline.py build --exe "dist/app.exe" --report-file build_report.xml --agent-run-id <build_run_id>
python pipeline.py gates prepare-phase --phase build
python pipeline.py gates phase-ci --phase build --repo hojiyong2-commits/Pipeline

python pipeline.py gates technical
python pipeline.py gates oracle
python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline
python pipeline.py gates accept --result ACCEPT --evidence <실제-결과물-경로-또는-첨부파일> --user-confirmed
python pipeline.py architect --report-file architect_report.xml
```

## 파이프라인 스모크 테스트

**Pipeline ID:** IMP-20260509-A4DB

이 섹션은 전체 파이프라인이 끝까지 정상 동작하는지 확인한 기록입니다.

### 이 테스트가 확인한 것

| 단계 | 확인한 내용 | 사용한 게이트 |
|---|---|---|
| Phase 1 — PM | 계약 초기화, 작업 분해, `step_plan.xml` 작성 | `pipeline.py done --phase pm --decomp --clarification --roadmap` |
| Phase 2 — Dev | 브랜치에서 README 수정, PR 흐름 확인 | `pipeline.py done --phase dev --files "README.md" --scope-declared` |
| Phase 4 — QA | 내용 검증, 브랜치/PR 검증 | `pipeline.py qa --result PASS --numeric-score N` |
| Phase 5 — SEC | 문서 작업이라 보안 검사는 생략 | `pipeline.py sec --skip` |
| Phase 6 — Build | 문서 작업이라 EXE 빌드는 해당 없음 | `pipeline.py build --exe "N/A" --skip-reason "docs-only"` |
| Phase A — 단계별 CI | PM/Dev/QA/Build마다 GitHub 자동 검증 통과 | `pipeline.py gates prepare-phase --phase dev` 후 `pipeline.py gates phase-ci --phase dev` |
| Phase 7 — Three-Gate | 기술 검사, Oracle 검사, GitHub 자동 검사, 사용자 승인 | `pipeline.py gates accept --result ACCEPT --evidence [실제-결과물-경로-또는-첨부파일] --user-confirmed` |
| Phase 8 — Architect | 원인 분석과 후속 개선 여부 판단 | `pipeline.py architect --report-file architect_report.xml` |

### 확인된 제약

- `main`에 직접 push하지 않고 PR을 거칩니다.
- GitHub 자동 검사(`pytest` + `pipeline_attestation.json`)가 통과해야 합니다.
- PM/Dev/QA/Build 단계마다 `phase_attestation.json` 검증이 필요합니다.
- Technical, Oracle, GitHub CI, User Acceptance 게이트는 필수입니다.
- 사용자가 승인한 결과물은 `G:\내 드라이브\터미널\<pipeline_id>`에 복사되고 `deployment_manifest.json`이 기록됩니다.

### Option A: 에이전트별 receipt + 단계별 CI

각 주요 단계는 한 번만 쓰는 agent receipt와 연결되어야 기록할 수 있습니다.

```powershell
python pipeline.py agent start --phase pm
# 출력된 token은 pm-agent에게만 전달
python pipeline.py agent finish --run-id <run_id> --token <token> --output-file step_plan.xml
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <run_id>
python pipeline.py gates prepare-phase --phase pm
git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add pm phase attestation request"
git push
python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline
```

`dev`, `qa`, `build`도 같은 방식으로 진행합니다. GitHub 자동 검사는 단계 요청, 증거 파일, agent receipt, 변경 번호, 테스트 실행 결과를 확인합니다.

이 구조에서는 PM이 Dev/QA/Build 역할을 대신했다고 주장하기 어렵습니다. 오케스트레이터가 token을 발급하고, 해당 phase agent만 그 token으로 receipt를 완성합니다. 이후 `pipeline.py`와 GitHub 자동 검사가 그 receipt를 확인합니다.

이 방식은 강한 운영상 방어입니다. 완전한 암호학적 증명을 원하면 로컬 agent가 수정할 수 없는 외부 runner나 별도 signer가 필요합니다.

### 사용자가 소유하는 oracle 파일

Dev 시작 전, PM은 사용자에게 실제 입력 예시와 기대 출력 예시를 받아야 합니다. 정답 파일은 아래 위치에 저장합니다.

```text
tests/oracles/<pipeline_id>/<case_id>/
```

그 다음 oracle 파일을 등록합니다:

```powershell
python pipeline.py contract add-oracle --input tests/oracles/<pipeline_id>/<case_id>/input.json --expected tests/oracles/<pipeline_id>/<case_id>/expected.json --case-kind normal
```

`pipeline.py contract audit`는 `tests/oracles/**` 밖의 oracle 파일을 거부합니다. CODEOWNERS도 `tests/oracles/**`, `tests/**`, 루트의 `test_*.py`, `*_test.py`를 사용자 확인 대상으로 표시합니다. 이렇게 해야 agent가 정답지나 테스트를 몰래 바꾸기 어렵습니다.

### PM 설계 확인

Dev로 넘어가기 전, PM은 모듈 분해안을 사용자에게 쉬운 한국어로 보여주고 확인받아야 합니다. 모듈이 1개뿐이어도 확인합니다.

`pipeline.py done --phase pm`은 `step_plan.xml`의 `<design_confirmation>`을 검사합니다. 각 중요한 질문은 근거, 왜 중요한지, 추천안, 최소 2개 옵션, 장점, 단점, 사용자 답변을 포함해야 합니다. 변수명이나 코드 취향처럼 중요하지 않은 질문은 묻지 않고, 유지보수성 우선 원칙으로 PM이 정리합니다.

### GitHub 자동 검사 흐름

`.github/workflows/ci.yml`은 모든 PR과 `main` push에서 실행됩니다.

1. `python -m pytest -q`를 실행합니다.
2. 성공하면 `pipeline_attestation.json`을 만듭니다.
3. PR에서는 `human_acceptance_packet.md`를 만들고, **최종 확인 안내** 댓글을 쉬운 한국어로 남깁니다.
4. `.pipeline/phase_attestation_request.json`이 있으면 단계별 증거를 확인하고 `phase_attestation.json`을 만듭니다.
5. 필요한 증명 파일을 GitHub 첨부파일로 올립니다.

`pipeline.py gates github-ci`는 최신 GitHub 자동 검사 결과를 확인합니다. `pipeline.py gates phase-ci --phase <pm|dev|qa|build>`는 다음 역할로 넘어가기 전에 단계별 증명을 확인합니다.

### 최종 사용자 확인

사용자가 마지막에 할 일은 이것뿐입니다:

1. 마지막 작업 담당자가 준 PR 링크를 엽니다.
2. PR 댓글의 **최종 확인 안내**를 읽습니다.
3. 그 안내에 적힌 결과 링크/경로 또는 첨부파일을 엽니다.
4. 결과물이 요청과 맞으면 Claude Code/dashboard에서 승인(ACCEPT), 아니면 거절(REJECT)을 고릅니다.

이 안내는 GitHub Actions가 자동으로 만듭니다. CI 통과 여부, 변경 파일,
중요 보호 파일 변경 여부, 첨부파일 링크를 요약합니다. 사용자는 코드를
검토하지 않고 결과물만 보고 판단하면 됩니다.

최종 확인 댓글은 PR 본문에서 `최종 판단 요약`, `사용자가 확인할 결과물`,
`기대 결과와 실제 결과`, `중요한 선택과 트레이드오프`를 읽어옵니다.
이 정보가 비어 있으면 댓글 맨 위에 `판단 정보 상태: 정보 부족`이 표시됩니다.
그 경우에는 승인하지 말고 담당자에게 보완을 요청하거나 거절하면 됩니다.

### GitHub 신뢰 기준

`main` 브랜치는 로컬 agent 흐름 밖에서 보호됩니다.

- `main` 직접 push는 막습니다.
- PR은 GitHub 자동 검사 `tests`를 통과해야 merge할 수 있습니다.
- 관리자도 보호 규칙을 따릅니다.
- `.github/CODEOWNERS`, `.github/workflows/**`, `pipeline.py`, `CLAUDE.md`, `.claude/agents/**`, `tests/oracles/**`, `tests/**`, `test_*.py`, `*_test.py`는 CODEOWNERS에 들어 있습니다.

이렇게 하면 최종 merge 결정과 자동 검사가 로컬 Claude/Codex 주장 밖에서 이루어집니다. 단일 소유자 repo에서 “승인 1명 필수”를 켜면 본인이 만든 PR이 막힐 수 있으므로, 그 설정은 신뢰할 두 번째 GitHub 계정을 추가한 뒤 켜는 것이 좋습니다.
