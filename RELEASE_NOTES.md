# Pipeline v1.0 릴리즈 노트

**릴리즈 날짜:** 2026-05-30
**기준 커밋:** PR #356 merge 후 최신 main
**태그:** v1.0 — PR #356 merge 후 main 최신화: `git pull origin main && git tag v1.0 && git push origin v1.0`

## v1.0 안정화 릴리즈

이 릴리즈는 지금까지 머지된 파이프라인 기능을 실사용 가능한 v1 기준선으로 고정합니다.
새 기능은 추가하지 않았으며, 문서와 최종 검증에 집중합니다.

## 포함된 핵심 IMP

| IMP ID | 기능 | 머지 커밋 |
|---|---|---|
| IMP-20260529-D8BA | Security & Secrets Boundary | 8b75482 |
| IMP-20260528-0A9E | Golden Task Regression Suite | 7125790 |
| IMP-20260528-3898 | Workspace Hygiene & Artifact Isolation | 23bb5ec |
| IMP-20260527-075A | Cost/Attempt Budget Gate | 8c20102 |
| IMP-20260526-82E3 | Observability Metrics Gate | c4a0f41 |
| IMP-20260524-48C4 | Oracle Quality Gate 강화 | 7becd26 |
| IMP-20260525-6FAC | Real CLI Path E2E Gate Policy | 94f8900 |

## 포함된 핵심 게이트

| 게이트 | 명령어 |
|---|---|
| Security & Secrets Boundary | `python pipeline.py gates secrets` |
| Golden Task | `python pipeline.py golden run --smoke` |
| Workspace Hygiene | `python pipeline.py gates preflight-pr-impl` |
| Cost/Attempt Budget | `python pipeline.py budget status` |
| Observability Metrics | `python pipeline.py metrics report` |
| Oracle Quality Gate | `python pipeline.py gates oracle` |
| Real CLI Path E2E | `pytest tests/e2e/ -v` |
| Phase Attestation (Option A) | `python pipeline.py gates phase-ci --phase pm` |

## 실제 업무 사용 가능 여부

**사용 가능합니다** — 아래 조건이 모두 충족됩니다:
- 전체 테스트 PASS: `pytest tests/ -q`
- GitHub Actions CI PASS (main 기준 최신 PR)
- Security & Secrets Boundary 활성화 (IMP-20260529-D8BA)
- Phase Attestation Option A 적용
- Oracle Quality Gate 적용

주의사항: 알려진 제한사항 5가지를 확인하고 사용하십시오 (README 참조).

## 알려진 제한사항

1. **Codex Review (GPT-5.5)**: OpenAI API 쿼터 소진 시 `review codex-record`로 수동 기록 필요
2. **Google Drive 배포**: `G:\내 드라이브\터미널\` 경로가 마운트되지 않으면 배포 단계 SKIP
3. **Windows 전용**: `pipeline.py`는 Windows PowerShell 환경에서 개발됨. Linux/macOS 경로 구분자 조정 필요
4. **Phase attestation**: GitHub Actions 미실행 환경(오프라인)에서는 phase-ci gate 수동 처리 필요
5. **git tag**: v1.0 태그 자동 생성 없음 — ACCEPT 후 사용자가 직접 실행

## 다음 단계

- v1.0 태그 생성: PR #356 merge 후 main 최신화 → `git pull origin main && git tag v1.0 && git push origin v1.0`
- v1.1 로드맵: 추후 별도 IMP 파이프라인으로 계획

## 부록: 게이트별 검증 명령

### Weekly Cleanup Archive (Hygiene)

7일 이상 된 임시 산출물을 스캔하거나 Google Drive 찌꺼기 폴더로 이동합니다.

```powershell
# 후보 파일 목록 확인 (파일 이동 없음)
python pipeline.py hygiene scan --older-than 7d --json

# 실제 이동 전 dry-run 확인
python pipeline.py hygiene archive --older-than 7d --json --dry-run

# 실제 이동 (PIPELINE_DEPLOY_ROOT 아래 찌꺼기/YYYY-MM-DD/ 폴더로 이동)
python pipeline.py hygiene archive --older-than 7d

# Windows 작업 스케줄러에 주간 자동 실행 등록 (dry-run)
python pipeline.py hygiene schedule install --dry-run

# Windows 작업 스케줄러에 실제 등록 (관리자 권한 필요)
python pipeline.py hygiene schedule install

# 스케줄 등록 상태 확인
python pipeline.py hygiene schedule status
```

**보호 파일 목록** (이동 대상에서 항상 제외):
- `pipeline.py`, `CLAUDE.md`, `README.md`, `RELEASE_NOTES.md`, `pyproject.toml`, `.gitignore`, `.gitattributes`
- `.github/`, `.claude/`, `.codex/`, `.pipeline/`, `tests/`, `pipeline_contracts/`, `pipeline_outputs/` 디렉토리

**이동 대상 패턴** (`HYGIENE_ARCHIVE_PATTERNS` SSoT 상수):
- `build_report*.xml`, `qa_report*.xml`, `dev_handover*.xml`, `architect_report*.xml`
- `failure_packet.json`, `acceptance_comment.json`, `bandit_e2e_result*.json`, `codex_review_result*.json` 등

**환경 변수**:
- `PIPELINE_DEPLOY_ROOT`: 찌꺼기 이동 대상 루트 (기본값: `G:\내 드라이브\터미널`)
- `PIPELINE_STATE_PATH`: 격리 테스트 시 state 파일 경로 지정

### Security & Secrets Boundary
```powershell
python pipeline.py gates secrets
# PR diff 포함 전체 스캔
python pipeline.py gates secrets --base-ref main
```

### Golden Task (smoke 테스트)
```powershell
python pipeline.py golden run --smoke
# 전체 테스트
python pipeline.py golden run --all
```

### Observability Metrics
```powershell
python pipeline.py metrics report
```

### Cost/Attempt Budget
```powershell
python pipeline.py budget status
```

### 전체 E2E 테스트
```powershell
pytest tests/e2e/ -v
```

### 전체 테스트 (빠른 확인)
```powershell
pytest tests/ -q
```

### Phase Attestation 상태 확인
```powershell
python pipeline.py gates status
python pipeline.py status
```
