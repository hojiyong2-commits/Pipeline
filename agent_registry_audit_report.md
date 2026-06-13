# Agent Registry Audit Report
## IMP-20260613-4A22: Agent Registry SSoT 정합성 확립

생성일: 2026-06-13
파이프라인: IMP-20260613-4A22

---

## 1. MODIFIED (이번 IMP에서 변경된 항목)

| 항목 | 변경 내용 | 파일 |
|---|---|---|
| pm-planner-agent.md 신규 생성 | 기존 pm-agent.md(호환성 문서)와 분리된 first-class active agent 정의 파일 생성. frontmatter: model=claude-opus-4-5, name=pm-planner-agent, phase_id=pm_planner, receipt_id=pm-planner-agent | `.claude/agents/pm-planner-agent.md` |
| drift 감지 테스트 신규 생성 | 26개 pytest 함수로 active agent md_path 실존, AGENTS.md vs PHASE_AGENT_IDS 일치, phase receipt expected id 일치, pipeline-manager first-class, pm-agent compat 구분, model tier 혼동 금지를 자동 검증 | `tests/test_agent_registry_4a22.py` |

---

## 2. PRESERVED (이번 IMP에서 변경 없이 보존된 신뢰 루트 항목)

| 항목 | 보존 이유 |
|---|---|
| `pipeline.py` PHASE_AGENT_IDS 상수 | 기존 값 그대로 유지. pm_planner→pm-planner-agent, pipeline_manager→pipeline-manager-agent, dev→dev-agent, qa→qa-agent, build→build-agent. 동작 변경 없음. |
| `pipeline.py` PHASE_RECEIPT_RUN_PHASES | pm→pm_planner 매핑 유지. 변경 없음. |
| `pipeline.py` AGENT_RUN_PHASES | pipeline_manager 포함 상태 유지. 변경 없음. |
| `pipeline.py` evidence integrity 함수들 | `_validate_evidence_provenance`, `_register_evidence_to_inventory`, `_check_oracle_manifest_vs_inventory`, `_auto_generate_final_packet_and_update_pr` — IMP-20260613-82ED 구현 보존. 수정 없음. |
| AGENTS.md 에이전트 표 | PM Planner Agent 행 포함, PM Agent "호환성 문서" 표기 유지. 구조 변경 없음. |
| Three-Gate / Option A / oracle gate | 완화 없음. 모든 외부 게이트 그대로 유지. |
| GPT advisory / Codex Review | manual diagnostic 상태 유지. ENABLE_GPT_ADVISORY_REQUIRED 미설정 기본값 유지. hard gate 복구 없음. |

---

## 3. DEPRECATED_OR_COMPAT (deprecated 또는 호환성 문서로 분류된 항목)

| 항목 | 분류 | 설명 |
|---|---|---|
| `pm-agent.md` | COMPAT | 호환성 문서. 기존 pm-agent 기반 파이프라인 참조용으로 유지됨. 새 파이프라인에서 active execution agent로 사용 금지. PHASE_AGENT_IDS의 "pm" key는 legacy 항목으로 유지. |
| `python pipeline.py done --phase pm --report-file` (pm-agent 단독 사용) | DEPRECATED_PATTERN | pm-agent 단독 사용 패턴. 신규 파이프라인은 pm-planner-agent + pipeline-manager-agent 분리 패턴 사용. |

---

## 4. DEFERRED (이번 IMP 범위 밖으로 연기된 항목)

| 항목 | 연기 이유 | 권고 후속 IMP |
|---|---|---|
| pipeline.py에 canonical registry JSON 신설 | 사용자 제약 "새 파이프라인 기능 추가 금지"에 의해 차단. 기존 세 소스(PHASE_AGENT_IDS / AGENTS.md 표 / .claude/agents/*.md)의 일치를 테스트로 검증하는 접근을 채택. | 향후 필요 시 별도 IMP |
| pm-agent.md 완전 삭제 | 기존 참조 파이프라인과의 호환성 파손 위험. compat 문서로 유지. | 모든 파이프라인이 planner/manager 분리 완료 후 별도 IMP |
| AGENTS.md "active/compat" 헤더 구분 추가 | 테스트가 이미 PASS 상태이므로 불필요한 변경을 유발하지 않음. 표 내 "호환성 문서" 텍스트로 충분히 구분됨. | 선택적 문서 개선 IMP |
| pipeline.py codex doctor required_files 갱신 | pm-planner-agent.md가 이미 존재하므로 현재 검사 PASS. 변경 불필요. | 해당 없음 |

---

## 요약

이번 IMP-20260613-4A22는 다음 두 가지 핵심 deliverable로 drift를 해소했습니다:

1. **pm-planner-agent.md 신규 생성**: AGENTS.md/CLAUDE.md 표에 등록된 PM Planner Agent가 실제 `.claude/agents/` 디렉토리에 first-class 파일로 존재하게 됨. 이후 `codex doctor`, drift 감지 테스트, 새 파이프라인이 pm-planner-agent를 올바르게 참조할 수 있음.

2. **drift 감지 테스트 26개**: 세 소스(pipeline.py PHASE_AGENT_IDS, AGENTS.md 에이전트 표, .claude/agents/*.md 파일)의 정합성을 자동으로 검증. 향후 에이전트 추가/제거/이름 변경 시 테스트 실패로 즉시 감지됨.

pipeline.py PHASE_AGENT_IDS 및 evidence integrity 로직은 변경 없이 보존되었으며, GPT advisory는 manual diagnostic 상태를 유지합니다.

## SCOPE NOTE: PR 내 IMP-20260612-CE06 파일 삭제 설명

이 PR 차이(diff)에 `.pipeline/phase_evidence/IMP-20260612-CE06/**` 9개 파일의 삭제가 포함되어 있습니다.

**이유**: `main` 브랜치에 IMP-20260612-CE06 파이프라인의 phase attestation 증거 파일 9개가 `.pipeline/phase_evidence/IMP-20260612-CE06/` 경로에 잔류하고 있었습니다. 이 파일들은 CE06 파이프라인 완료 후 phase-attestation 브랜치가 main에 머지될 때 포함된 것으로, CLAUDE.md 정책("`phase_evidence/**`는 main에서 무시되어야 함")에 따라 main에 있어서는 안 됩니다.

4A22 impl 브랜치는 이 파일들을 포함하지 않으므로, 이 PR 머지 시 main에서 해당 파일들이 삭제됩니다. 이는 CLAUDE.md `.gitignore` 정책에 따른 의도된 정리(cleanup)이며 4A22 기능 변경과는 무관합니다.

**영향**: IMP-20260612-CE06 파이프라인은 이미 완료되었으므로 이 파일 삭제는 운영에 영향을 주지 않습니다.
