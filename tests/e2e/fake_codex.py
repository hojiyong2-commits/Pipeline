#!/usr/bin/env python3
# [Purpose]: IMP-20260717-5EE0 MT-11(REJECT#3 P0-3) — E2E 테스트용 가짜 Codex CLI. 실제 Codex를
#            호출하지 않고 --output-last-message 파일에 구성 가능한 verdict JSON을 기록한다.
# [Assumptions]: 호출자가 `... --output-last-message <path> ...` 인자를 넘긴다. verdict/findings는
#            FAKE_CODEX_VERDICT / FAKE_CODEX_FINDINGS 환경변수로 제어한다.
# [Vulnerability & Risks]: 테스트 전용. 프로덕션 코드가 이 파일을 import/실행하지 않는다.
# [Improvement]: exit code나 stdout JSONL 이벤트도 환경변수로 구성 가능하게 확장할 수 있다.
"""Fake Codex CLI for E2E testing.

Usage: fake_codex.py --output-last-message <path> [other args...]
Writes a configurable verdict JSON to the --output-last-message file.
Reads verdict config from FAKE_CODEX_VERDICT env var (default: APPROVE_TO_USER).
"""
import json
import os
import pathlib
import sys


def main() -> None:
    """--output-last-message 파일에 verdict JSON을 기록하고 exit code 0으로 종료한다."""
    args = sys.argv[1:]
    output_path = None
    i = 0
    while i < len(args):
        if args[i] == "--output-last-message" and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        else:
            i += 1

    verdict = os.environ.get("FAKE_CODEX_VERDICT", "APPROVE_TO_USER")
    findings = json.loads(os.environ.get("FAKE_CODEX_FINDINGS", "[]"))

    result = {"verdict": verdict, "findings": findings}

    if output_path:
        pathlib.Path(output_path).write_text(
            json.dumps(result), encoding="utf-8"
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
