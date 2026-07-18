#!/usr/bin/env python3
"""fake_codex.py — TC-44~TC-52 테스트용 가짜 Codex CLI executable.

실제 `codex exec --json` 명령의 형식을 재현하되:
1. sys.argv를 캡처하여 파일로 저장 (TC-48이 읽음)
2. --output-last-message <path> 인자를 파싱하여 해당 경로에 verdict JSON 기록
3. stdout에 JSONL 감사 이벤트 출력 (실제 codex exec --json 형식 재현)
4. exit 0

환경변수:
    FAKE_CODEX_ARGV_FILE: sys.argv를 저장할 파일 경로 (없으면 저장 안 함)
    FAKE_CODEX_VERDICT: 반환할 verdict ('APPROVE_TO_USER' 기본값, 'REJECT'도 가능)
    FAKE_CODEX_CALL_COUNT_FILE: 호출 횟수를 누적하는 파일 경로 (없으면 무시)
"""
import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    argv = sys.argv[1:]

    # 1) argv 캡처 (FAKE_CODEX_ARGV_FILE 지정 시 저장)
    argv_file = os.environ.get("FAKE_CODEX_ARGV_FILE", "")
    if argv_file:
        try:
            Path(argv_file).write_text(json.dumps(argv, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # 2) 호출 횟수 카운터 누적 (FAKE_CODEX_CALL_COUNT_FILE 지정 시)
    count_file = os.environ.get("FAKE_CODEX_CALL_COUNT_FILE", "")
    if count_file:
        try:
            _p = Path(count_file)
            _cur = int(_p.read_text(encoding="utf-8").strip() or "0") if _p.exists() else 0
            _p.write_text(str(_cur + 1), encoding="utf-8")
        except OSError:
            pass

    # 3) --output-last-message <path> 파싱
    output_path = ""
    for i, arg in enumerate(argv):
        if arg == "--output-last-message" and i + 1 < len(argv):
            output_path = argv[i + 1]
            break

    # 4) verdict 결정
    verdict = os.environ.get("FAKE_CODEX_VERDICT", "APPROVE_TO_USER")

    # 5) verdict JSON 파일 작성
    verdict_json = {
        "verdict": verdict,
        "findings": [],
        "schema_version": 6,
        "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "review_id": "fake-review-" + str(int(time.time())),
        "model_used": "gpt-5.6-sol",
        "pipeline_id": os.environ.get("FAKE_CODEX_PIPELINE_ID", "IMP-TEST"),
    }
    if output_path:
        try:
            Path(output_path).write_text(
                json.dumps(verdict_json, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            print(json.dumps({"type": "error", "message": str(exc)}))
            sys.exit(1)

    # 6) stdout JSONL 이벤트 (실제 codex exec --json 형식 재현)
    events = [
        {"type": "session_started", "model": "gpt-5.6-sol"},
        {"type": "reasoning", "content": "Analyzing code changes..."},
        {"type": "message", "role": "assistant", "content": json.dumps(verdict_json)},
        {"type": "session_ended", "exit_code": 0},
    ]
    for ev in events:
        print(json.dumps(ev, ensure_ascii=False))

    sys.exit(0)


if __name__ == "__main__":
    main()
