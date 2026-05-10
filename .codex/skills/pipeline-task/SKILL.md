---
name: pipeline-task
description: Use when Codex is asked to run this repository's mandatory task pipeline, especially prompts starting with /task, ./task, "task", "Task", "파이프라인 돌려", "task 스킬", or an explicit [$pipeline-task] skill mention. Mirrors Claude Code's /task command with Three-Gate, Option A phase attestation, PM role split, Incremental Module Gate, Korean user-facing reports, GitHub Actions, and final ACCEPT/REJECT. No shortcuts are allowed after an explicit skill call.
---

# Pipeline Task Skill

## 목적

이 저장소에서 Codex가 작업을 맡으면 Claude Code의 `/task`와 같은 파이프라인을 사용한다. Classic mode는 사용하지 않는다.

사용자가 Codex에서 아래처럼 말하면 이 skill을 사용한다.

- `/task [작업 내용]`
- `./task [작업 내용]`
- `task 스킬로 [작업 내용]`
- `파이프라인 돌려서 [작업 내용]`
- `[$pipeline-task](...) [작업 내용]`

`./task`는 실제 쉘 스크립트 이름이 아니라 Codex에게 이 skill을 쓰라는 호출 문구로 해석한다.

필수 구조:

1. Three-Gate는 항상 사용한다.
2. Option A phase attestation은 항상 사용한다.
3. Incremental Module Gate는 항상 사용한다.
4. PM은 `pm-planner-agent`와 `pipeline-manager-agent`로 분리한다.
5. 최종 사용자는 결과물 보고서와 링크만 보고 `ACCEPT` 또는 `REJECT`를 결정한다.

## No Shortcut Rule

사용자가 이 skill을 명시적으로 호출하면 간단한 업무라도 전체 파이프라인을 생략하지 않는다.

- 직접 코드 수정, 직접 빌드, 직접 배포를 먼저 하지 않는다.
- 기존 파이프라인을 이어서 쓰려면 사용자가 같은 작업의 계속 진행이라고 명시해야 한다. 아니면 새 pipeline id로 시작한다.
- `PM planner receipt -> pipeline manager receipt -> Incremental Module Gate -> Dev/QA/Build phase attestation -> Technical/Oracle/GitHub CI -> final ACCEPT/REJECT -> Architect complete` 순서를 끝까지 따른다.
- 사용자가 "빨리", "그냥 수정", "간단히"라고 말해도 shortcut으로 처리하지 않는다. 대신 예상 시간과 줄인 범위를 한국어로 보고한다.
- `G:\내 드라이브\터미널` 배포는 final `ACCEPT` 이후에만 pipeline 완료 배포로 기록한다. 사용자가 별도 임시 배포를 명시하면 그 결과는 pipeline COMPLETE가 아니다.

## LLM Model Lock

이 skill로 실행되는 모든 LLM 역할은 `gpt-5.5`를 사용한다.

- Codex에서 모델 선택이 가능한 PM planner, pipeline manager, Dev, QA, Security, Build, Harness/diagnostic, Architect 역할은 모두 `gpt-5.5`로 고정한다.
- `gpt-5.5`를 사용할 수 없으면 조용히 다른 모델로 낮추지 말고 중단한 뒤 사용자에게 보고한다.
- 이 skill이 명시 호출된 작업에서는 Claude나 다른 LLM으로 최종 검증을 대체하지 않는다.
- `pipeline.py`, GitHub Actions, pytest, ruff, mypy, bandit, oracle 비교 같은 결정론적 도구는 LLM이 아니므로 그대로 사용한다.

## 시작 전 확인

먼저 아래 명령으로 Codex 훅 상태를 확인한다.

```bash
python pipeline.py codex doctor
```

`PASS`가 아니면 작업을 시작하지 말고 실패 항목을 먼저 고친다.

사용자에게는 첫 응답에서 짧게 말한다: "Codex용 `/task` 파이프라인으로 진행하겠습니다." 이후 진행 설명, 도구 설명, 최종 보고는 쉬운 한국어로 쓴다.

## 필수 PM 분리 흐름

Codex가 직접 구현을 시작하기 전에 PM 설계와 관리 인계를 분리해야 한다.

```bash
python pipeline.py agent start --phase pm_planner
python pipeline.py agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml

python pipeline.py agent start --phase pipeline_manager
python pipeline.py agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml

python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml
```

`manager_handoff.xml`은 다음 사실을 증명해야 한다.

- `pipeline-manager-agent`가 실행 관리를 맡았다.
- `step_plan.xml`의 SHA-256을 확인했다.
- planner run id를 확인했다.
- step plan을 수정하지 않겠다고 선언했다.
- 다음 단계가 `dev`임을 선언했다.

## Codex 작업 원칙

- 사용자에게 보이는 설명은 쉬운 한국어로 쓴다.
- 코드가 아니라 결과물을 기준으로 최종 판단할 수 있게 만든다.
- 중간 단계에서 임의로 PASS를 주장하지 않는다.
- receipt token은 해당 역할의 output을 기록할 때만 사용한다.
- PM 산출물 안에 Dev/QA/Build 산출물을 섞지 않는다.
- PM 설계 전 `.claude/agents/shared/anti_gaming_rules.md`를 읽고 `<anti_gaming_read>true</anti_gaming_read>`를 `step_plan.xml`에 남긴다.
- Dev는 `scope_manifest.json` 밖의 파일을 건드리지 않는다.
- QA는 자신이 만든 테스트만 믿지 않고 실제 산출물, oracle, GitHub Actions 결과를 확인한다.

## 최종 사용자 보고

마지막 보고는 아래 정보를 포함한다.

- 결과물 링크 또는 로컬 경로
- GitHub PR 링크
- GitHub Actions 실행 링크
- 사용자가 확인해야 할 항목
- `ACCEPT` 또는 `REJECT` 기준

사용자에게 코드를 읽으라고 요구하지 않는다.

## 실패 시 행동

게이트가 실패하면 실패한 게이트 이름, 원인, 사용자가 볼 결과물, 다음 재시도 범위를 짧게 보고한다. 임의로 PASS를 만들거나 점수로 완료를 주장하지 않는다.
