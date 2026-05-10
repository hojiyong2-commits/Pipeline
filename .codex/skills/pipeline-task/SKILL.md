---
name: pipeline-task
description: Use when Codex is asked to run this repository's mandatory task pipeline, especially prompts starting with /task, ./task, "task", "Task", "파이프라인 돌려", or "task 스킬". Mirrors Claude Code's /task command with Three-Gate, Option A phase attestation, PM role split, Incremental Module Gate, Korean user-facing reports, GitHub Actions, and final ACCEPT/REJECT.
---

# Pipeline Task Skill

## 목적

이 저장소에서 Codex가 작업을 맡으면 Claude Code의 `/task`와 같은 파이프라인을 사용한다. Classic mode는 사용하지 않는다.

사용자가 Codex에서 아래처럼 말하면 이 skill을 사용한다.

- `/task [작업 내용]`
- `./task [작업 내용]`
- `task 스킬로 [작업 내용]`
- `파이프라인 돌려서 [작업 내용]`

`./task`는 실제 쉘 스크립트 이름이 아니라 Codex에게 이 skill을 쓰라는 호출 문구로 해석한다.

필수 구조:

1. Three-Gate는 항상 사용한다.
2. Option A phase attestation은 항상 사용한다.
3. Incremental Module Gate는 항상 사용한다.
4. PM은 `pm-planner-agent`와 `pipeline-manager-agent`로 분리한다.
5. 최종 사용자는 결과물 보고서와 링크만 보고 `ACCEPT` 또는 `REJECT`를 결정한다.

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
