---
name: pipeline-task
description: Use when Codex is asked to run this repository's mandatory task pipeline, especially prompts starting with /task, ./task, "task", "Task", "파이프라인 돌려", "task 스킬", or an explicit [$pipeline-task] skill mention. Mirrors Claude Code's /task command with Three-Gate, Option A phase attestation, PM role split, Incremental Module Gate, Korean user-facing reports, GitHub Actions, and final ACCEPT/REJECT. No shortcuts are allowed after an explicit skill call.
---

# Pipeline Task Skill

## 목적

Codex에서 이 저장소의 작업을 맡으면 Claude Code의 `/task`와 같은 파이프라인을 따른다. Classic mode는 사용하지 않는다.

트리거:

- `/task [작업]`
- `./task [작업]`
- `task [작업]`
- `파이프라인 돌려 [작업]`
- `[$pipeline-task](...) [작업]`

## No Shortcut Rule

1. Three-Gate는 항상 사용한다.
2. Option A phase attestation은 항상 사용한다.
3. Incremental Module Gate는 항상 사용한다.
4. PM은 `pm-planner-agent`와 `pipeline-manager-agent` 역할로 분리한다.
5. 사용자는 마지막에 결과물 보고서/링크만 보고 `ACCEPT` 또는 `REJECT`를 결정한다.
6. 명시적으로 이 skill이 호출되면 간단한 업무라도 파이프라인을 생략하지 않는다.

## 모델 고정

이 skill로 수행하는 모든 LLM 역할은 `gpt-5.5`를 사용한다. 사용할 수 없으면 조용히 다른 모델로 대체하지 말고 사용자에게 보고한다.

## 시작 전 확인

```powershell
python pipeline.py codex doctor
```

PASS가 아니면 작업을 시작하지 말고 실패 항목을 먼저 고친다.

## PM 설계 확인 게이트

PM은 micro-task가 1개뿐이어도 반드시 사용자에게 분해안을 보여주고 명시 답변을 받아야 한다.

사용자에게 보여줄 질문은 쉬운 한국어여야 한다.

- 무엇을 어떻게 쪼갰는지
- 왜 이 쪼개기가 중요한지
- 추천안
- 선택지 2개 이상
- 각 선택지의 장점과 단점

사용자가 답한 뒤에만 Pipeline Manager가 아래 명령을 실행한다.

```powershell
python pipeline.py confirm-design --question-id DQ-1 --selected-option A --answer "사용자가 실제로 답한 문장" --mt-id MT-1 --module-split --user-confirmed
```

금지:

- `사용자 원칙에 따라 A로 진행`
- `PM이 판단`
- `에이전트가 추론`
- 이전 대화의 취향을 근거로 사용자 답변을 대신 작성

`confirm-design` 기록이 없으면 `done --phase pm`은 실패해야 한다.

## PM 흐름

```powershell
python pipeline.py agent start --phase pm_planner
python pipeline.py agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml

python pipeline.py agent start --phase pipeline_manager
python pipeline.py agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml

python pipeline.py confirm-design --question-id DQ-1 --selected-option A --answer "<verbatim user answer>" --mt-id MT-1 --module-split --user-confirmed

python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml
```

`manager_handoff.xml`은 planner receipt, step plan hash, 다음 phase가 `dev`임을 증명해야 한다.

`step_plan.xml`에는 반드시 `<anti_gaming_read>true</anti_gaming_read>`가 있어야 한다.

## Phase Attestation

각 주요 phase 뒤에는 다음을 실행한다.

```powershell
python pipeline.py gates prepare-phase --phase <pm|dev|qa|build>
git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add <phase> phase attestation request"
git push
python pipeline.py gates phase-ci --phase <phase> --repo hojiyong2-commits/Pipeline
```

속도를 위해 phase CI는 receipt/hash/schema/evidence만 검증한다. 전체 `pytest`는 최종 GitHub CI gate에서 한 번 강하게 실행한다.

## Dev/QA/Build

Dev는 PM micro-task 범위 밖 파일을 수정하지 않는다. 각 MT는 `module design → module dev → module qa PASS` 순서로 처리한다.

QA는 실제 증거를 확인하고 자기 채점만으로 PASS를 주장하지 않는다.

Build는 결과물을 만들고, 최종 배포는 사용자의 `ACCEPT` 뒤에만 기록한다.

## 최종 보고

마지막 보고에는 다음을 포함한다.

- 결과물 링크 또는 로컬 경로
- GitHub PR 링크
- GitHub Actions 링크
- 사용자가 확인해야 할 항목
- `ACCEPT` / `REJECT` 기준

사용자에게 코드를 읽으라고 요구하지 않는다.

최종 User Acceptance 뒤에는 Architect complete 단계까지 기록한다.
