#!/usr/bin/env python3
# [Purpose]: Claude Code Stop hook helper. assistant 최종 출력에서 "사용자 승인 요청"
#   블록(5요소)을 감지하면 Codex CLI로 PR 검토를 보조 수행한다. 이 helper는 절대로
#   PR 댓글을 쓰거나 ACCEPT 코드를 게시하거나 gates accept를 실행하지 않는다 — 사용자에게
#   결과만 출력하여 사용자가 직접 승인하도록 돕는다 (User Acceptance 자동화 금지).
# [Assumptions]: gh CLI와 codex CLI가 PATH에 존재하고 인증되어 있다. transcript는
#   --transcript 인자(JSONL 또는 텍스트)로 전달되며, 마지막 assistant 메시지에 승인 블록이 있다.
#   .pipeline/ 디렉토리는 프로젝트 루트 하위에 쓰기 가능하다.
# [Vulnerability & Risks]: gh/codex 응답 형식이 예고 없이 바뀌면 파싱이 깨질 수 있다.
#   이를 위해 모든 외부 호출은 fail-closed(예외 시 exit 1)로 처리한다. transcript 파싱은
#   인용/코드블록 내부 예시를 무시하여 사용자 프롬프트 오탐을 방지한다. 형식이 모호한
#   verdict는 ValueError로 차단한다.
# [Improvement]: 시간이 더 있다면 Codex 응답을 구조화 JSON 스키마로 강제하고,
#   gh GraphQL로 단일 호출 packet 조회, 그리고 검토 결과 캐시 TTL을 추가할 것이다.
"""Codex 사용자 승인 검토 보조 hook helper.

이 모듈은 Claude Code의 Stop hook에서 호출된다. assistant 최종 출력에
"사용자 승인 요청" 블록(5요소)이 있으면 Codex CLI로 PR을 한 번 더 검토하고,
그 결과를 사용자에게만 출력한다. 어떤 경우에도 PR 댓글 게시나 gates accept
실행을 수행하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- 모듈 레벨 정규식 1회 컴파일 (Rule D2: 함수 내 재컴파일 금지) ---
# GitHub PR URL: https://github.com/<owner>/<repo>/pull/<number>
_PR_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+/pull/\d+"
)
# ACCEPT 코드: ACCEPT-<pipeline_id>[-<nonce>]
_ACCEPT_CODE_RE = re.compile(r"ACCEPT-[A-Za-z0-9_\-]+")
# verdict 형식: 정확히 APPROVE_TO_USER 또는 'REJECT - <사유>'
_APPROVE_RE = re.compile(r"^APPROVE_TO_USER$")
_REJECT_RE = re.compile(r"^REJECT\s*-\s*.+$", re.DOTALL)

# 트리거 5요소 마커 (같은 블록 안에 모두 존재해야 함)
_MARKER_APPROVAL = "사용자 승인 요청"
_MARKER_PR = "PR:"
_MARKER_CODE_LABEL = "승인 코드:"
_MARKER_CODEX_REQUIRED = "CODEX 검토 필요"

# transcript tail에서 검사할 마지막 assistant 메시지 수
_TAIL_MESSAGES = 5
# REJECT 누적 횟수 상한 (초과 시 사용자 개입 요청)
_MAX_REJECT = 3

_ENCODINGS = ("utf-8", "utf-8-sig", "cp949", "latin-1")


def _read_text_with_fallback(path: Path) -> str:
    """utf-8 → utf-8-sig → cp949 → latin-1 순서 인코딩 fallback 읽기.

    Args:
        path: 읽을 파일 경로.
    Returns:
        파일 텍스트 내용.
    Raises:
        TypeError: path가 None이거나 Path/str가 아닌 경우.
        FileNotFoundError: 파일이 없는 경우.
        UnicodeDecodeError: 어떤 인코딩으로도 디코드 불가한 경우.
    """
    if path is None:
        raise TypeError("path must not be None")
    if not isinstance(path, (str, Path)):
        raise TypeError(f"path must be str or Path, got {type(path).__name__}")
    path = Path(path)
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnicodeDecodeError(
        "utf-8", b"", 0, 1, f"Cannot decode {path} with any supported encoding"
    )


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """원자적 JSON 쓰기 (tempfile → os.replace).

    Args:
        path: 쓰기 대상 경로.
        data: 직렬화할 dict.
    Raises:
        TypeError: 입력이 None인 경우.
    """
    if path is None:
        raise TypeError("path must not be None")
    if data is None:
        raise TypeError("data must not be None")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, str(path))
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _strip_code_fences_and_quotes(text: str) -> str:
    """코드블록(```...```)과 인용(> ...) 라인을 제거하여 예시 블록 오탐을 방지.

    Args:
        text: 원본 텍스트.
    Returns:
        코드블록/인용 라인이 제거된 텍스트.
    Raises:
        TypeError: text가 None이거나 str가 아닌 경우.
    """
    if text is None:
        raise TypeError("text must not be None")
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    out_lines: List[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            # 코드블록 펜스 토글 — 펜스 라인 자체와 내부는 모두 제거
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith(">"):
            # 인용 라인은 사용자 프롬프트 예시로 간주하여 제거
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def parse_acceptance_block(text: str) -> Optional[Dict[str, Any]]:
    """transcript tail에서 5요소를 모두 포함한 승인 요청 블록을 탐지.

    5요소: (1) "사용자 승인 요청", (2) "PR:" + GitHub pull URL,
    (3) "승인 코드:", (4) "ACCEPT-<pipeline_id>", (5) 블록 내 마지막
    의미 있는 줄이 "CODEX 검토 필요"여야 함.
    인용(>)/코드블록(```) 내부 예시 블록은 무시한다.

    Args:
        text: 검사할 transcript tail 텍스트.
    Returns:
        5요소 모두 충족 시 {"block": str, "pr_url": str, "accept_code": str},
        하나라도 미충족 시 None.
    Raises:
        TypeError: text가 None이거나 str가 아닌 경우.
    """
    if text is None:
        raise TypeError("text must not be None")
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    if len(text) == 0:
        return None

    # 인용/코드블록 예시 제거 (AC-3)
    cleaned = _strip_code_fences_and_quotes(text)

    # 마지막 의미 있는 줄이 "CODEX 검토 필요"여야 함 (AC-1, AC-2)
    meaningful = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not meaningful:
        return None
    if meaningful[-1] != _MARKER_CODEX_REQUIRED:
        return None

    # 나머지 4요소 마커 존재 확인
    if _MARKER_APPROVAL not in cleaned:
        return None
    if _MARKER_PR not in cleaned:
        return None
    if _MARKER_CODE_LABEL not in cleaned:
        return None

    pr_url, accept_code = extract_pr_url_and_code(cleaned)
    if pr_url is None or accept_code is None:
        return None

    return {"block": cleaned, "pr_url": pr_url, "accept_code": accept_code}


def extract_pr_url_and_code(block: str) -> Tuple[Optional[str], Optional[str]]:
    """블록 텍스트에서 PR URL과 ACCEPT 승인 코드를 추출.

    Args:
        block: 승인 요청 블록 텍스트.
    Returns:
        (pr_url, accept_code) 튜플. 미발견 시 해당 항목은 None.
    Raises:
        TypeError: block이 None이거나 str가 아닌 경우.
    """
    if block is None:
        raise TypeError("block must not be None")
    if not isinstance(block, str):
        raise TypeError(f"block must be str, got {type(block).__name__}")
    pr_match = _PR_URL_RE.search(block)
    code_match = _ACCEPT_CODE_RE.search(block)
    pr_url = pr_match.group(0) if pr_match else None
    accept_code = code_match.group(0) if code_match else None
    return pr_url, accept_code


def check_dedupe(
    pr_url: str, accept_code: str, head_sha: str, state_path: Path
) -> bool:
    """(pr_url, accept_code, head_sha) 조합 기반 중복 검토 방지.

    동일 조합이 state 파일에 이미 있으면 True(중복 → 건너뜀).
    head_sha가 바뀌면 같은 PR/코드여도 False(재검토 허용, AC-5).

    Args:
        pr_url: PR URL.
        accept_code: ACCEPT 승인 코드.
        head_sha: PR head 커밋 SHA.
        state_path: codex_review_state.json 경로.
    Returns:
        중복(이미 검토됨)이면 True, 신규(검토 필요)면 False.
    Raises:
        TypeError: 인자가 None이거나 타입이 잘못된 경우.
        ValueError: 문자열 인자가 빈 문자열인 경우.
    """
    for name, val in (
        ("pr_url", pr_url),
        ("accept_code", accept_code),
        ("head_sha", head_sha),
    ):
        if val is None:
            raise TypeError(f"{name} must not be None")
        if not isinstance(val, str):
            raise TypeError(f"{name} must be str, got {type(val).__name__}")
        if len(val.strip()) == 0:
            raise ValueError(f"{name} must not be empty")
    if state_path is None:
        raise TypeError("state_path must not be None")
    state_path = Path(state_path)

    key = f"{pr_url}|{accept_code}|{head_sha}"
    if not state_path.exists():
        return False
    try:
        raw = _read_text_with_fallback(state_path)
        state = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        # 손상된 state는 신규로 취급 (검토 진행). 이후 재기록으로 복구.
        return False
    reviewed = state.get("reviewed", [])
    if not isinstance(reviewed, list):
        return False
    return key in reviewed


def _record_dedupe(
    pr_url: str, accept_code: str, head_sha: str, state_path: Path
) -> None:
    """검토 완료된 조합을 state 파일에 기록 (원자적 쓰기).

    Args:
        pr_url: PR URL.
        accept_code: ACCEPT 승인 코드.
        head_sha: PR head SHA.
        state_path: codex_review_state.json 경로.
    """
    state_path = Path(state_path)
    key = f"{pr_url}|{accept_code}|{head_sha}"
    state: Dict[str, Any] = {"reviewed": []}
    if state_path.exists():
        try:
            raw = _read_text_with_fallback(state_path)
            loaded = json.loads(raw) if raw.strip() else {}
            if isinstance(loaded, dict) and isinstance(
                loaded.get("reviewed"), list
            ):
                state = loaded
        except (json.JSONDecodeError, OSError):
            state = {"reviewed": []}
    if key not in state["reviewed"]:
        state["reviewed"].append(key)
    _atomic_write_json(state_path, state)


def _run_gh(args: List[str]) -> str:
    """gh CLI 호출 후 stdout 반환. 실패 시 RuntimeError (fail-closed).

    Args:
        args: gh 뒤에 붙일 인자 목록.
    Returns:
        stdout 텍스트.
    Raises:
        RuntimeError: gh 미설치 또는 호출 실패.
    """
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except FileNotFoundError as e:
        raise RuntimeError("gh CLI not found — fail-closed") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"gh CLI timed out: {' '.join(args)}") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"gh CLI failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def build_review_packet(
    pr_url: str, accept_code: str, head_sha: str
) -> Dict[str, Any]:
    """gh CLI로 PR 검토 packet 수집 (title/body/files/diff/CI/comments).

    Args:
        pr_url: PR URL.
        accept_code: ACCEPT 승인 코드.
        head_sha: PR head SHA.
    Returns:
        검토 packet dict.
    Raises:
        TypeError: 인자가 None이거나 타입이 잘못된 경우.
        ValueError: 문자열 인자가 빈 문자열인 경우.
        RuntimeError: gh 조회 실패 (fail-closed).
    """
    for name, val in (
        ("pr_url", pr_url),
        ("accept_code", accept_code),
        ("head_sha", head_sha),
    ):
        if val is None:
            raise TypeError(f"{name} must not be None")
        if not isinstance(val, str):
            raise TypeError(f"{name} must be str, got {type(val).__name__}")
        if len(val.strip()) == 0:
            raise ValueError(f"{name} must not be empty")

    view_raw = _run_gh(
        [
            "pr",
            "view",
            pr_url,
            "--json",
            "title,body,files,statusCheckRollup,comments,headRefName,number",
        ]
    )
    try:
        view = json.loads(view_raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("gh pr view returned non-JSON — fail-closed") from e

    diff = _run_gh(["pr", "diff", pr_url])
    # diff는 일부만 포함 (대용량 방지)
    diff_excerpt = diff[:8000]

    files = view.get("files", []) or []
    changed_files = [f.get("path", "") for f in files if isinstance(f, dict)]

    ci_rollup = view.get("statusCheckRollup", []) or []
    ci_status = [
        {
            "name": c.get("name") or c.get("context", ""),
            "conclusion": c.get("conclusion") or c.get("state", ""),
        }
        for c in ci_rollup
        if isinstance(c, dict)
    ]

    comments = view.get("comments", []) or []
    latest_comments = [
        {"author": (c.get("author") or {}).get("login", ""), "body": c.get("body", "")}
        for c in comments[-3:]
        if isinstance(c, dict)
    ]

    return {
        "pr_url": pr_url,
        "accept_code": accept_code,
        "head_sha": head_sha,
        "title": view.get("title", ""),
        "body": view.get("body", ""),
        "changed_files": changed_files,
        "diff_excerpt": diff_excerpt,
        "ci_status": ci_status,
        "latest_comments": latest_comments,
    }


def call_codex_cli(prompt: str) -> str:
    """codex CLI를 호출하여 검토 verdict 텍스트를 받는다 (fail-closed).

    Args:
        prompt: Codex에 전달할 검토 프롬프트. 금지 사항이 포함되어야 함.
    Returns:
        Codex stdout (verdict 텍스트).
    Raises:
        TypeError: prompt가 None이거나 str가 아닌 경우.
        ValueError: prompt가 빈 문자열인 경우.
        RuntimeError: codex 미설치 또는 호출 실패 (fail-closed).
    """
    if prompt is None:
        raise TypeError("prompt must not be None")
    if not isinstance(prompt, str):
        raise TypeError(f"prompt must be str, got {type(prompt).__name__}")
    if len(prompt.strip()) == 0:
        raise ValueError("prompt must not be empty")
    try:
        result = subprocess.run(
            ["codex", "exec", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
    except FileNotFoundError as e:
        raise RuntimeError("codex CLI not found — fail-closed") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("codex CLI timed out — fail-closed") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"codex CLI failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _build_codex_prompt(packet: Dict[str, Any]) -> str:
    """검토 packet으로부터 Codex 프롬프트를 구성 (금지 사항 명시).

    Args:
        packet: build_review_packet 반환 dict.
    Returns:
        Codex 검토 프롬프트 문자열.
    """
    files_txt = "\n".join(f"  - {p}" for p in packet.get("changed_files", []))
    ci_txt = "\n".join(
        f"  - {c['name']}: {c['conclusion']}" for c in packet.get("ci_status", [])
    )
    return (
        "당신은 사용자 ACCEPT 직전의 마지막 검토자입니다. 아래 PR을 검토하세요.\n\n"
        "엄격한 금지 사항 (절대 준수):\n"
        "  1. 파일을 수정하지 마세요.\n"
        "  2. PR 댓글을 작성하지 마세요.\n"
        "  3. 승인 코드(ACCEPT-...)를 어디에도 게시하지 마세요.\n"
        "  4. pipeline.py gates accept를 실행하지 마세요.\n"
        "  5. merge 또는 deploy를 실행하지 마세요.\n\n"
        "당신의 출력은 정확히 다음 두 형식 중 하나여야 합니다:\n"
        "  - 통과: APPROVE_TO_USER\n"
        "  - 반려: REJECT - <한 줄 사유>\n\n"
        f"PR URL: {packet.get('pr_url', '')}\n"
        f"제목: {packet.get('title', '')}\n"
        f"변경 파일:\n{files_txt}\n"
        f"CI 상태:\n{ci_txt}\n\n"
        f"본문 발췌:\n{packet.get('body', '')[:2000]}\n\n"
        f"Diff 발췌:\n{packet.get('diff_excerpt', '')}\n"
    )


def process_verdict(
    verdict: str, accept_code: str, pr_url: str, reject_count: int
) -> Dict[str, Any]:
    """Codex verdict 형식을 검증하고 사용자용 출력을 구성.

    verdict는 정확히 "APPROVE_TO_USER" 또는 "REJECT - <사유>" 형식이어야 한다.
    형식 위반 시 ValueError (fail-closed).
    REJECT: 사유를 그대로 출력, PR 댓글 작성/ACCEPT 코드 게시 안 함.
            reject_count가 3회 초과면 사용자 개입 요청 추가.
    APPROVE_TO_USER: 안내문 + 승인 코드만 출력 (게시/gates accept 안 함).

    Args:
        verdict: Codex가 반환한 verdict 텍스트.
        accept_code: ACCEPT 승인 코드.
        pr_url: PR URL.
        reject_count: 현재까지 누적된 REJECT 횟수.
    Returns:
        {"decision": "APPROVE_TO_USER"|"REJECT", "message": str,
         "post_pr_comment": False, "run_gates_accept": False,
         "needs_user_intervention": bool}.
    Raises:
        TypeError: 인자가 None이거나 타입이 잘못된 경우.
        ValueError: verdict 형식이 위반된 경우, 또는 빈 문자열인 경우.
    """
    if verdict is None:
        raise TypeError("verdict must not be None")
    if not isinstance(verdict, str):
        raise TypeError(f"verdict must be str, got {type(verdict).__name__}")
    if accept_code is None:
        raise TypeError("accept_code must not be None")
    if not isinstance(accept_code, str):
        raise TypeError(
            f"accept_code must be str, got {type(accept_code).__name__}"
        )
    if pr_url is None:
        raise TypeError("pr_url must not be None")
    if not isinstance(pr_url, str):
        raise TypeError(f"pr_url must be str, got {type(pr_url).__name__}")
    if reject_count is None:
        raise TypeError("reject_count must not be None")
    if not isinstance(reject_count, int) or isinstance(reject_count, bool):
        raise TypeError(
            f"reject_count must be int, got {type(reject_count).__name__}"
        )
    # reject_count는 0 이상이어야 함 — 음수 불허 (보정 금지)
    if reject_count < 0:
        raise ValueError(  # negative not allowed: 누적 횟수는 0 이상만 의미 있음
            f"reject_count must be >= 0, got {reject_count}"
        )
    if len(verdict.strip()) == 0:
        raise ValueError("verdict must not be empty")

    normalized = verdict.strip()

    if _APPROVE_RE.match(normalized):
        message = (
            "Codex 검토 통과.\n"
            "PR 댓글에 아래 코드를 직접 한 줄로 입력해 주세요.\n"
            f"{accept_code}"
        )
        return {
            "decision": "APPROVE_TO_USER",
            "message": message,
            "post_pr_comment": False,
            "run_gates_accept": False,
            "needs_user_intervention": False,
        }

    if _REJECT_RE.match(normalized):
        new_count = reject_count + 1
        needs_intervention = new_count > _MAX_REJECT
        message = normalized
        if needs_intervention:
            message = (
                f"{normalized}\n\n"
                f"Codex 검토가 {new_count}회 연속 반려되었습니다 "
                f"(상한 {_MAX_REJECT}회 초과). 사용자 개입이 필요합니다."
            )
        return {
            "decision": "REJECT",
            "message": message,
            "post_pr_comment": False,
            "run_gates_accept": False,
            "needs_user_intervention": needs_intervention,
        }

    raise ValueError(
        "Codex verdict 형식 위반 — 'APPROVE_TO_USER' 또는 'REJECT - <사유>'만 허용. "
        f"받은 값: {normalized[:80]!r}"
    )


def _mask_accept_code(accept_code: str) -> str:
    """audit log용 승인 코드 마스킹 (앞 8자 + ****).

    Args:
        accept_code: 원본 승인 코드.
    Returns:
        앞 8자만 노출하고 나머지는 ****로 가린 문자열.
    """
    if not isinstance(accept_code, str) or len(accept_code) == 0:
        return "****"
    return accept_code[:8] + "****"


def _append_audit_log(
    audit_path: Path,
    pr_url: str,
    head_sha: str,
    accept_code: str,
    verdict: str,
    result: str,
    dedupe_skip: bool,
) -> None:
    """audit log에 한 줄 추가 (accept_code 마스킹).

    Args:
        audit_path: codex_review_audit.log 경로.
        pr_url: PR URL.
        head_sha: PR head SHA.
        accept_code: 승인 코드 (마스킹되어 기록됨).
        verdict: Codex verdict 원문 (또는 사유 요약).
        result: 처리 결과 요약.
        dedupe_skip: 중복으로 건너뛰었는지 여부.
    """
    audit_path = Path(audit_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    masked = _mask_accept_code(accept_code)
    safe_verdict = (verdict or "").replace("\n", " ")[:120]
    line = (
        f"{ts}\tpr={pr_url}\thead_sha={head_sha}\taccept_code={masked}\t"
        f"verdict={safe_verdict}\tresult={result}\tdedupe_skip={dedupe_skip}\n"
    )
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _extract_assistant_tail(transcript_text: str, n_messages: int) -> str:
    """transcript에서 마지막 assistant 메시지 N개의 텍스트를 결합.

    transcript가 JSONL(한 줄당 {"role","content"}) 형식이면 assistant 메시지만
    추출하고, 일반 텍스트면 마지막 N*40줄을 tail로 사용한다.

    Args:
        transcript_text: transcript 전체 텍스트.
        n_messages: 추출할 마지막 assistant 메시지 수.
    Returns:
        결합된 assistant tail 텍스트.
    Raises:
        TypeError: transcript_text가 None이거나 str가 아닌 경우.
        ValueError: n_messages가 0 이하인 경우.
    """
    if transcript_text is None:
        raise TypeError("transcript_text must not be None")
    if not isinstance(transcript_text, str):
        raise TypeError(
            f"transcript_text must be str, got {type(transcript_text).__name__}"
        )
    if n_messages <= 0:
        raise ValueError(f"n_messages must be > 0, got {n_messages}")

    assistant_msgs: List[str] = []
    is_jsonl = False
    for line in transcript_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("role") == "assistant":
            is_jsonl = True
            content = obj.get("content", "")
            if isinstance(content, list):
                # content가 블록 리스트인 경우 text만 모음
                text_parts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            if isinstance(content, str):
                assistant_msgs.append(content)

    if is_jsonl and assistant_msgs:
        return "\n".join(assistant_msgs[-n_messages:])

    # JSONL이 아니면 일반 텍스트의 tail 라인 사용
    lines = transcript_text.splitlines()
    tail = lines[-(n_messages * 40):]
    return "\n".join(tail)


def _project_pipeline_dir() -> Path:
    """프로젝트 루트의 .pipeline 디렉토리 경로를 반환.

    PIPELINE_STATE_PATH 환경 변수가 있으면 그 부모를, 없으면 cwd 기준 .pipeline.

    Returns:
        .pipeline 디렉토리 절대 경로.
    """
    env_state = os.environ.get("PIPELINE_STATE_PATH")
    if env_state:
        return Path(env_state).resolve().parent / ".pipeline"
    return (Path.cwd() / ".pipeline").resolve()


def _get_pr_head_sha(pr_url: str) -> str:
    """gh CLI로 PR head 커밋 SHA를 조회 (fail-closed).

    Args:
        pr_url: PR URL.
    Returns:
        PR head 커밋 SHA.
    Raises:
        RuntimeError: 조회 실패 (fail-closed).
    """
    raw = _run_gh(["pr", "view", pr_url, "--json", "headRefOid"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("gh pr view headRefOid non-JSON — fail-closed") from e
    sha = data.get("headRefOid", "")
    if not sha:
        raise RuntimeError("PR head SHA empty — fail-closed")
    return str(sha)


def main(argv: Optional[List[str]] = None) -> int:
    """hook 진입점. transcript에서 승인 블록 탐지 후 Codex 검토를 수행.

    Args:
        argv: 명령행 인자 (테스트용 주입 가능). None이면 sys.argv 사용.
    Returns:
        프로세스 종료 코드. 정상/블록 없음/중복 = 0, fail-closed 오류 = 1.
    """
    parser = argparse.ArgumentParser(
        description="Codex 사용자 승인 검토 보조 hook"
    )
    parser.add_argument(
        "--transcript",
        nargs="?",
        default=os.environ.get("CLAUDE_HOOK_TRANSCRIPT_PATH", ""),
        help="transcript 파일 경로 (JSONL 또는 텍스트). 생략 가능.",
    )
    args = parser.parse_args(argv)

    transcript_path = (args.transcript or "").strip()
    if not transcript_path:
        # transcript 없음 — 조용히 종료 (정상)
        return 0

    tpath = Path(transcript_path)
    if not tpath.exists():
        # transcript 파일 없음 — 조용히 종료 (정상)
        return 0

    try:
        transcript_text = _read_text_with_fallback(tpath)
    except (OSError, UnicodeDecodeError):
        # 읽기 실패는 조용히 종료 (블록 판정 불가)
        return 0

    tail = _extract_assistant_tail(transcript_text, _TAIL_MESSAGES)
    block = parse_acceptance_block(tail)
    if block is None:
        # 승인 블록 없음 — 조용히 종료 (정상)
        return 0

    pr_url = block["pr_url"]
    accept_code = block["accept_code"]

    pipeline_dir = _project_pipeline_dir()
    state_path = pipeline_dir / "codex_review_state.json"
    audit_path = pipeline_dir / "codex_review_audit.log"

    # --- fail-closed 영역 시작 ---
    try:
        head_sha = _get_pr_head_sha(pr_url)
    except RuntimeError as e:
        print(f"[CODEX REVIEW] PR head SHA 조회 실패 (fail-closed): {e}", file=sys.stderr)
        return 1

    try:
        is_dupe = check_dedupe(pr_url, accept_code, head_sha, state_path)
    except (TypeError, ValueError) as e:
        print(f"[CODEX REVIEW] dedupe 검사 실패 (fail-closed): {e}", file=sys.stderr)
        return 1

    if is_dupe:
        _append_audit_log(
            audit_path, pr_url, head_sha, accept_code,
            verdict="(skipped)", result="dedupe_skip", dedupe_skip=True,
        )
        print(
            "[CODEX REVIEW] 동일 PR/승인코드/SHA 조합은 이미 검토됨 — 건너뜁니다.",
        )
        return 0

    try:
        packet = build_review_packet(pr_url, accept_code, head_sha)
    except (RuntimeError, TypeError, ValueError) as e:
        print(f"[CODEX REVIEW] PR packet 수집 실패 (fail-closed): {e}", file=sys.stderr)
        return 1

    try:
        prompt = _build_codex_prompt(packet)
        verdict = call_codex_cli(prompt)
    except (RuntimeError, TypeError, ValueError) as e:
        print(f"[CODEX REVIEW] Codex 호출 실패 (fail-closed): {e}", file=sys.stderr)
        return 1

    # 누적 REJECT 횟수 조회
    reject_count = _read_reject_count(state_path)

    try:
        outcome = process_verdict(verdict, accept_code, pr_url, reject_count)
    except (TypeError, ValueError) as e:
        print(
            f"[CODEX REVIEW] Codex 응답 형식 위반 (fail-closed): {e}",
            file=sys.stderr,
        )
        return 1

    # 검토 완료 기록 (dedupe + reject_count 갱신)
    _record_dedupe(pr_url, accept_code, head_sha, state_path)
    if outcome["decision"] == "REJECT":
        _bump_reject_count(state_path)
    else:
        _reset_reject_count(state_path)

    _append_audit_log(
        audit_path, pr_url, head_sha, accept_code,
        verdict=verdict, result=outcome["decision"], dedupe_skip=False,
    )

    print(outcome["message"])
    return 0


def _read_reject_count(state_path: Path) -> int:
    """state 파일에서 누적 REJECT 횟수를 읽는다.

    Args:
        state_path: codex_review_state.json 경로.
    Returns:
        누적 REJECT 횟수 (없으면 0).
    """
    state_path = Path(state_path)
    if not state_path.exists():
        return 0
    try:
        raw = _read_text_with_fallback(state_path)
        state = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return 0
    val = state.get("reject_count", 0)
    return val if isinstance(val, int) and not isinstance(val, bool) else 0


def _mutate_reject_count(state_path: Path, new_value: int) -> None:
    """state 파일의 reject_count를 갱신 (원자적 쓰기).

    Args:
        state_path: codex_review_state.json 경로.
        new_value: 설정할 값 (0 이상).
    """
    state_path = Path(state_path)
    state: Dict[str, Any] = {"reviewed": [], "reject_count": 0}
    if state_path.exists():
        try:
            raw = _read_text_with_fallback(state_path)
            loaded = json.loads(raw) if raw.strip() else {}
            if isinstance(loaded, dict):
                state = loaded
                state.setdefault("reviewed", [])
        except (json.JSONDecodeError, OSError):
            state = {"reviewed": [], "reject_count": 0}
    state["reject_count"] = max(0, new_value)
    _atomic_write_json(state_path, state)


def _bump_reject_count(state_path: Path) -> None:
    """누적 REJECT 횟수를 1 증가."""
    _mutate_reject_count(state_path, _read_reject_count(state_path) + 1)


def _reset_reject_count(state_path: Path) -> None:
    """누적 REJECT 횟수를 0으로 리셋 (APPROVE 시)."""
    _mutate_reject_count(state_path, 0)


if __name__ == "__main__":
    sys.exit(main())
