import json, pathlib

state_path = pathlib.Path("pipeline_state.json")
state = json.loads(state_path.read_text(encoding="utf-8"))

# AC별 구현 근거와 검증 테스트명
AC_EVIDENCE = {
    "AC-1": {
        "result": "PASS",
        "linked_mt": ["MT-1"],
        "implementation_evidence": [
            "MT-1: pipeline.py: _compute_packet_sha256 — 신규 헬퍼 함수, packet 파일 SHA256 계산",
            "MT-1: pipeline.py: _cmd_gates_request_accept 수정 — packet_path/packet_sha256/packet_frozen_at 3개 필드를 acceptance_request.json에 저장 (schema_version=2)"
        ],
        "verification": [
            "MT-1 qa: PASS — test_request_accept_stores_packet_sha256 (test_freeze_guard_9934.py) — request-accept 후 acceptance_request.json에 packet_sha256 존재 확인"
        ]
    },
    "AC-2": {
        "result": "PASS",
        "linked_mt": ["MT-2"],
        "implementation_evidence": [
            "MT-2: pipeline.py: _check_packet_freeze_status — 신규 헬퍼 함수, packet SHA256 현재값과 저장값 비교",
            "MT-2: pipeline.py: _cmd_gates_accept 수정 — evidence_sha256 검증 후 packet_sha256 검증 단계 추가. 불일치 시 status=STALE_PACKET + [FINAL PACKET FREEZE] 차단"
        ],
        "verification": [
            "MT-2 qa: PASS — test_gates_accept_fails_when_packet_changed (test_freeze_guard_9934.py) — packet 변경 후 accept 차단",
            "MT-2 qa: PASS — test_packet_change_marks_status_stale_packet (test_freeze_guard_9934.py) — STALE_PACKET 상태 확인"
        ]
    },
    "AC-3": {
        "result": "PASS",
        "linked_mt": ["MT-3"],
        "implementation_evidence": [
            "MT-3: pipeline.py: _cmd_report_final_packet 수정 — status=PENDING + packet_sha256 기록 시 [FINAL PACKET FREEZE] 메시지 + exit code 1로 차단"
        ],
        "verification": [
            "MT-3 qa: PASS — test_report_final_packet_blocked_when_pending (test_freeze_guard_9934.py) — PENDING 상태에서 final-packet 재생성 차단 확인"
        ]
    },
    "AC-4": {
        "result": "PASS",
        "linked_mt": ["MT-3"],
        "implementation_evidence": [
            "MT-3: pipeline.py: _cmd_report_final_packet --force-new-request 옵션 추가 — 기존 PENDING request를 EXPIRED(reason=final_packet_regenerated)로 변경 후 새 packet 생성 (새 nonce 없음)"
        ],
        "verification": [
            "MT-3 qa: PASS — test_force_new_request_expires_pending_and_creates_new_packet (test_freeze_guard_9934.py) — --force-new-request 후 EXPIRED 상태 + 새 packet 생성 확인"
        ]
    },
    "AC-5": {
        "result": "PASS",
        "linked_mt": ["MT-1"],
        "implementation_evidence": [
            "MT-1: pipeline.py: _cmd_gates_request_accept --force-new-code 옵션 추가 — 기존 PENDING request를 EXPIRED(reason=user_forced_new_code)로 변경 + 새 nonce 발급 + 새 packet_sha256 저장"
        ],
        "verification": [
            "MT-1 qa: PASS — test_force_new_code_expires_pending_and_issues_new_nonce (test_freeze_guard_9934.py) — --force-new-code 후 EXPIRED + 새 nonce + 새 packet_sha256 확인"
        ]
    },
    "AC-6": {
        "result": "PASS",
        "linked_mt": ["MT-1"],
        "implementation_evidence": [
            "MT-1: pipeline.py: _should_reuse_acceptance_nonce 수정 — packet_sha256 동등성 비교 추가. PR/CI/evidence/packet 모두 같으면 기존 nonce 재사용 + [재사용] 메시지 출력"
        ],
        "verification": [
            "MT-1 qa: PASS — test_request_accept_reuses_nonce_when_all_match (test_freeze_guard_9934.py) — 동일 조건 2회 실행 시 nonce 재사용 확인"
        ]
    },
    "AC-7": {
        "result": "PASS",
        "linked_mt": ["MT-3"],
        "implementation_evidence": [
            "MT-3: pipeline.py: _cmd_report_update_pr_body 수정 — PENDING request 존재 시 packet_sha256 일치 검증 추가. 불일치 시 [FINAL PACKET FREEZE] + exit code 1. packet 파일 재생성 금지 (읽기만)"
        ],
        "verification": [
            "MT-3 qa: PASS — test_update_pr_body_does_not_regenerate_packet (test_freeze_guard_9934.py) — update-pr-body가 packet 파일을 수정하지 않음 확인",
            "MT-3 qa: PASS — test_update_pr_body_fails_on_packet_sha256_mismatch (test_freeze_guard_9934.py) — packet_sha256 불일치 시 차단 확인"
        ]
    },
    "AC-8": {
        "result": "PASS",
        "linked_mt": ["MT-2"],
        "implementation_evidence": [
            "MT-2: pipeline.py: _cmd_gates_accept 유지 — 기존 Nonce Gate(--user-confirmed 단독 차단, --acceptance-code 필수) 무손상. 새 packet_sha256 검증은 기존 검증 통과 후 추가 실행"
        ],
        "verification": [
            "MT-2 qa: PASS — test_user_confirmed_alone_still_blocked_nonce_gate_preserved (test_freeze_guard_9934.py) — --user-confirmed 단독 사용 시 acceptance_code_required 차단 확인"
        ]
    }
}

# structured_acceptance_criteria 업데이트
sac = state.get("structured_acceptance_criteria", [])
updated_sac = 0
for item in sac:
    ac_id = item.get("ac_id")
    if ac_id in AC_EVIDENCE:
        ev = AC_EVIDENCE[ac_id]
        item["result"] = ev["result"]
        item["linked_mt"] = ev["linked_mt"]
        item["implementation_evidence"] = ev["implementation_evidence"]
        item["verification"] = ev["verification"]
        updated_sac += 1

# requirements_tracking.acceptance_criteria 업데이트
rt = state.get("requirements_tracking", {})
rt_acs = rt.get("acceptance_criteria", [])
updated_rt = 0
for item in rt_acs:
    ac_id = item.get("ac_id")
    if ac_id in AC_EVIDENCE:
        ev = AC_EVIDENCE[ac_id]
        item["result"] = ev["result"]
        item["linked_mt"] = ev["linked_mt"]
        item["implementation_evidence"] = ev["implementation_evidence"]
        item["verification"] = ev["verification"]
        updated_rt += 1

state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"pipeline_state.json AC 업데이트 완료: structured_acceptance_criteria={updated_sac}개, requirements_tracking={updated_rt}개")
