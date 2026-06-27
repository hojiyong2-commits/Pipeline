#!/usr/bin/env python3
# [Purpose]: Claude Code Stop hook helper. assistant 최종 출력에서 "사용자 승인 요청"
#   블록(5요소)을 감지하면 Codex CLI로 PR 검토를 보조 수행한다 (IMP-20260626-4121 Codex
#   Review Loop). REJECT 피드백은 단 한 글자도 바꾸지 않고 stdout에 그대로 출력 후 exit 2로
#   재주입한다(Stop hook 재주입 방식). APPROVE 시에는 사용자 승인 요청 안내 + ACCEPT-<pipeline_id>
#   코드만 출력한다. 이 helper는 절대로 PR 댓글을 쓰거나 nonce를 노출하거나 gates accept를
#   실행하지 않는다. 모든 루프 상태는 .pipeline/codex_review_loop_state.json에만 저장하며,
#   acceptance_request.json은 읽거나 수정하지 않는다.
# [Assumptions]: gh CLI와 codex CLI가 PATH에 존재하고 인증되어 있다. Claude Code Stop hook은
#   hook 데이터를 stdin JSON으로 전달하며, 그 안의 last_assistant_message 필드에 승인 블록이
#   있다. transcript 파일 경로 파싱은 더 이상 필요 없다.
#   .pipeline/ 디렉토리는 프로젝트 루트 하위에 쓰기 가능하다.
# [Vulnerability & Risks]: gh/codex 응답 형식이 예고 없이 바뀌면 파싱이 깨질 수 있다.
#   이를 위해 모든 외부 호출은 fail-closed(예외 시 exit 1)로 처리한다. last_assistant_message
#   파싱은 인용/코드블록 내부 예시를 무시하여 사용자 프롬프트 오탐을 방지한다. 형식이 모호한
#   verdict는 ValueError로 차단한다. REJECT 원문은 prefix/suffix/번역/요약 없이 그대로 출력한다.
# [Improvement]: 시간이 더 있다면 Codex 응답을 구조화 JSON 스키마로 강제하고,
#   gh GraphQL로 단일 호출 packet 조회, 그리고 검토 결과 캐시 TTL을 추가할 것이다.
"""Codex 사용자 승인 검토 보조 hook helper (Codex Review Loop, IMP-20260626-4121).

이 모듈은 Claude Code의 Stop hook에서 호출된다. stdin JSON의 last_assistant_message에
"사용자 승인 요청" 블록(5요소)이 있으면 Codex CLI로 PR을 한 번 더 검토한다.

- REJECT - <사유>: 원문을 그대로 stdout에 출력하고 exit 2로 재주입한다.
  같은 (pipeline_id, pr_head_sha, packet_sha256, reject_reason) 조합 반복 시
  중복 주입을 차단(조용히 exit 0)하고, reject_count가 상한(5)을 초과하면
  루프를 자동 중단한다.
- APPROVE_TO_USER: 사용자 승인 요청 안내 + ACCEPT-<pipeline_id> 코드만 출력하고 exit 0.
  이미 같은 (pr_head_sha, packet_sha256)로 APPROVED면 Codex CLI를 다시 호출하지 않고
  같은 APPROVE 출력만 반복한다.

어떤 경우에도 PR 댓글 게시, nonce 노출, gates accept 실행을 수행하지 않는다.
모든 루프 상태는 .pipeline/codex_review_loop_state.json에만 저장된다.
"""

from __future__ import annotations

import hashlib
import importlib.util
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
# pipeline_id: FEAT|BUG|IMP-YYYYMMDD-XXXX
_PIPELINE_ID_RE = re.compile(r"(?:FEAT|BUG|IMP)-\d{8}-[A-Za-z0-9]+")
# verdict 형식: 정확히 APPROVE_TO_USER 또는 'REJECT - <사유>'
_APPROVE_RE = re.compile(r"^APPROVE_TO_USER$")
_REJECT_RE = re.compile(r"^REJECT\s*-\s*.+$", re.DOTALL)

# 트리거 5요소 마커 (같은 블록 안에 모두 존재해야 함)
_MARKER_APPROVAL = "사용자 승인 요청"
_MARKER_PR = "PR:"
_MARKER_CODE_LABEL = "승인 코드:"
_MARKER_ACCEPT_PREFIX = "ACCEPT-"
_MARKER_CODEX_REQUIRED = "CODEX 검토 필요"

# REJECT 누적 횟수 상한 — 초과(즉 6번째)부터 루프 자동 중단
_MAX_REJECT = 5

# Codex Review Loop 상태 파일명 (acceptance_request.json과 무관한 별도 파일)
_LOOP_STATE_FILENAME = "codex_review_loop_state.json"

_ENCODINGS = ("utf-8", "utf-8-sig", "cp949", "latin-1")

# IMP-20260627-3907: 승인 요청문 renderer + Codex Review Contract 단일 SSoT 경로.
# pipeline.py 전체 import는 side effect를 유발하므로, renderer는 importlib로
# .claude/acceptance_renderer.py를 직접 로드한다. contract도 동일 디렉토리에 위치.
_RENDERER_FILENAME = "acceptance_renderer.py"
_CONTRACT_FILENAME = "codex_review_contract.md"


def _load_renderer() -> Any:
    """.claude/acceptance_renderer.py를 importlib로 로드하여 모듈을 반환한다.

    pipeline.py 전체를 import하면 side effect가 발생하므로, renderer만 단독 로드한다.

    Returns:
        로드된 acceptance_renderer 모듈 객체.
    Raises:
        RuntimeError: renderer 파일이 없거나 로드 실패 시 (fail-closed).
    """
    renderer_path = Path(__file__).parent.parent / _RENDERER_FILENAME
    if not renderer_path.exists():
        raise RuntimeError(
            f"acceptance_renderer를 로드할 수 없습니다: {renderer_path}"
        )
    try:
        spec = importlib.util.spec_from_file_location(
            "acceptance_renderer", str(renderer_path)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"acceptance_renderer spec 생성 실패: {renderer_path}"
            )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (OSError, ImportError, SyntaxError) as e:
        raise RuntimeError(
            f"acceptance_renderer 로드 실패 (fail-closed): {e}"
        ) from e


def _contract_path() -> Path:
    """Codex Review Contract(.claude/codex_review_contract.md) 절대 경로를 반환한다.

    Returns:
        contract 파일 경로 (hook 파일과 같은 .claude 디렉토리 하위).
    """
    return Path(__file__).parent.parent / _CONTRACT_FILENAME


def _render_approve(pipeline_id: str, pr_url: Optional[str]) -> str:
    """APPROVE 시 사용자에게 출력할 메시지를 renderer B형으로 구성.

    renderer.render_user_acceptance_request(mode='user_final', ...)을 단일 SSoT로
    사용한다. nonce를 노출하지 않고 ACCEPT-<pipeline_id> 형식만 출력한다.

    Args:
        pipeline_id: 파이프라인 ID.
        pr_url: PR 링크 (없으면 None → '(PR 링크 없음)').
    Returns:
        APPROVE 사용자 출력 메시지 (renderer B형).
    Raises:
        TypeError: pipeline_id가 None이거나 str가 아닌 경우.
        RuntimeError: renderer 로드 실패 시 (fail-closed).
    """
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(
            f"pipeline_id must be str, got {type(pipeline_id).__name__}"
        )
    if pr_url is not None and not isinstance(pr_url, str):
        raise TypeError(
            f"pr_url must be str or None, got {type(pr_url).__name__}"
        )
    pr_line = pr_url if (pr_url and pr_url.strip()) else "(PR 링크 없음)"
    renderer = _load_renderer()
    return renderer.render_user_acceptance_request(
        mode="user_final", pr_url=pr_line, pipeline_id=pipeline_id
    )


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
        5요소 모두 충족 시 {"block": str, "pr_url": str, "accept_code": str,
        "pipeline_id": str}, 하나라도 미충족 시 None.
    Raises:
        TypeError: text가 None이거나 str가 아닌 경우.
    """
    if text is None:
        raise TypeError("text must not be None")
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    if len(text) == 0:
        return None

    # 인용/코드블록 예시 제거
    cleaned = _strip_code_fences_and_quotes(text)

    # 마지막 의미 있는 줄이 "CODEX 검토 필요"여야 함
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
    if _MARKER_ACCEPT_PREFIX not in cleaned:
        return None

    pr_url, accept_code = extract_pr_url_and_code(cleaned)
    if pr_url is None or accept_code is None:
        return None

    pipeline_id = _extract_pipeline_id(accept_code) or _extract_pipeline_id(cleaned)
    if pipeline_id is None:
        return None

    return {
        "block": cleaned,
        "pr_url": pr_url,
        "accept_code": accept_code,
        "pipeline_id": pipeline_id,
    }


def _extract_pipeline_id(text: str) -> Optional[str]:
    """문자열에서 pipeline_id(FEAT|BUG|IMP-YYYYMMDD-XXXX)를 추출.

    Args:
        text: 검색 대상 문자열.
    Returns:
        매치된 pipeline_id 또는 None.
    Raises:
        TypeError: text가 None이거나 str가 아닌 경우.
    """
    if text is None:
        raise TypeError("text must not be None")
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    m = _PIPELINE_ID_RE.search(text)
    return m.group(0) if m else None


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


def _sha256_text(text: str) -> str:
    """텍스트의 SHA-256 16진 다이제스트 (packet_sha256 산출용).

    Args:
        text: 해시할 문자열.
    Returns:
        SHA-256 hex 다이제스트.
    Raises:
        TypeError: text가 None이거나 str가 아닌 경우.
    """
    if text is None:
        raise TypeError("text must not be None")
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_loop_state(state_path: Path) -> Dict[str, Any]:
    """codex_review_loop_state.json 로드. 없거나 파싱 오류 시 빈 dict.

    Args:
        state_path: codex_review_loop_state.json 경로.
    Returns:
        파싱된 dict 또는 {} (파일 없음/JSON 오류).
    Raises:
        TypeError: state_path가 None인 경우.
    """
    if state_path is None:
        raise TypeError("state_path must not be None")
    state_path = Path(state_path)
    if not state_path.exists():
        return {}
    try:
        raw = _read_text_with_fallback(state_path)
        loaded = json.loads(raw) if raw.strip() else {}
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _record_state(state_path: Path, payload: Dict[str, Any]) -> None:
    """codex_review_loop_state.json에 루프 상태를 원자적으로 기록.

    Args:
        state_path: codex_review_loop_state.json 경로.
        payload: 기록할 상태 dict (status/pipeline_id 등 포함).
    Raises:
        TypeError: 인자가 None인 경우.
    """
    if state_path is None:
        raise TypeError("state_path must not be None")
    if payload is None:
        raise TypeError("payload must not be None")
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be dict, got {type(payload).__name__}")
    _atomic_write_json(Path(state_path), payload)


def _is_duplicate_reject(
    state: Dict[str, Any],
    pipeline_id: str,
    pr_head_sha: str,
    packet_sha256: str,
    reject_reason: str,
) -> bool:
    """직전 기록과 동일한 REJECT 조합인지 판정 (중복 주입 차단용).

    동일 (pipeline_id, pr_head_sha, packet_sha256, last_reject_reason) 조합이
    이미 REJECTED 상태로 기록되어 있으면 True.

    Args:
        state: 로드된 loop state dict.
        pipeline_id: 파이프라인 ID.
        pr_head_sha: PR head 커밋 SHA.
        packet_sha256: packet SHA-256.
        reject_reason: REJECT 원문 사유.
    Returns:
        중복(이미 같은 조합으로 REJECTED)이면 True, 신규면 False.
    Raises:
        TypeError: 인자가 None이거나 타입이 잘못된 경우.
    """
    for name, val in (
        ("state", state),
    ):
        if val is None:
            raise TypeError(f"{name} must not be None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be dict, got {type(state).__name__}")
    for sname, sval in (
        ("pipeline_id", pipeline_id),
        ("pr_head_sha", pr_head_sha),
        ("packet_sha256", packet_sha256),
        ("reject_reason", reject_reason),
    ):
        if sval is None:
            raise TypeError(f"{sname} must not be None")
        if not isinstance(sval, str):
            raise TypeError(f"{sname} must be str, got {type(sval).__name__}")
    if state.get("status") != "REJECTED":
        return False
    return (
        str(state.get("pipeline_id", "")) == pipeline_id
        and str(state.get("pr_head_sha", "")) == pr_head_sha
        and str(state.get("packet_sha256", "")) == packet_sha256
        and str(state.get("last_reject_reason", "")) == reject_reason
    )


def _check_stale(
    state: Dict[str, Any],
    pr_head_sha: str,
    packet_sha256: str,
    pipeline_id: str = "",
) -> bool:
    """기존 APPROVED 상태가 현재 head/packet/pipeline_id와 같아 재호출 불필요인지 판정.

    동일 (pr_head_sha, packet_sha256)로 이미 APPROVED면 True (재트리거 방지).
    pipeline_id가 주어지면 pipeline_id 일치까지 확인하여, 다른 파이프라인의
    APPROVED 상태를 stale로 오판하는 것을 차단한다.

    Args:
        state: 로드된 loop state dict.
        pr_head_sha: 현재 PR head 커밋 SHA.
        packet_sha256: 현재 packet SHA-256.
        pipeline_id: 현재 파이프라인 ID (빈 문자열이면 비교 생략 — 하위 호환).
    Returns:
        같은 APPROVED 상태가 유효(stale 아님)하면 True, 아니면 False.
    Raises:
        TypeError: 인자가 None이거나 타입이 잘못된 경우.
    """
    if state is None:
        raise TypeError("state must not be None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be dict, got {type(state).__name__}")
    for name, val in (
        ("pr_head_sha", pr_head_sha),
        ("packet_sha256", packet_sha256),
        ("pipeline_id", pipeline_id),
    ):
        if val is None:
            raise TypeError(f"{name} must not be None")
        if not isinstance(val, str):
            raise TypeError(f"{name} must be str, got {type(val).__name__}")
    if state.get("status") != "APPROVED":
        return False
    # pipeline_id가 주어진 경우 불일치하면 stale 아님 (다른 파이프라인의 APPROVED)
    if pipeline_id and str(state.get("pipeline_id", "")) != pipeline_id:
        return False
    return (
        str(state.get("pr_head_sha", "")) == pr_head_sha
        and str(state.get("packet_sha256", "")) == packet_sha256
    )


def _run_gh(args: List[str]) -> str:
    """gh CLI 호출 후 stdout 반환. 실패 시 RuntimeError (fail-closed).

    Args:
        args: gh 뒤에 붙일 인자 목록.
    Returns:
        stdout 텍스트.
    Raises:
        TypeError: args가 None이거나 list가 아닌 경우.
        RuntimeError: gh 미설치 또는 호출 실패.
    """
    if args is None:
        raise TypeError("args must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
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
    """검토 packet으로부터 Codex 프롬프트를 구성 (Codex Review Contract 삽입).

    IMP-20260627-3907: Codex Review Contract(.claude/codex_review_contract.md)를
    renderer.load_contract로 로드하여 프롬프트 상단에 삽입한다. contract 로드 실패 시
    RuntimeError를 전파하여(fail-closed) contract 없이 검토가 진행되는 것을 막는다.

    Args:
        packet: build_review_packet 반환 dict.
    Returns:
        Codex 검토 프롬프트 문자열 (contract 11개 항목 포함).
    Raises:
        TypeError: packet이 None이거나 dict가 아닌 경우.
        RuntimeError: contract 로드 실패 시 (fail-closed).
    """
    if packet is None:
        raise TypeError("packet must not be None")
    if not isinstance(packet, dict):
        raise TypeError(f"packet must be dict, got {type(packet).__name__}")

    # Codex Review Contract 로드 (fail-closed: 실패 시 RuntimeError 전파).
    # renderer.load_contract는 파일 부재/읽기 실패 시 RuntimeError를 발생시킨다.
    renderer = _load_renderer()
    contract_text = renderer.load_contract(str(_contract_path()))

    files_txt = "\n".join(f"  - {p}" for p in packet.get("changed_files", []))
    ci_txt = "\n".join(
        f"  - {c['name']}: {c['conclusion']}" for c in packet.get("ci_status", [])
    )
    return (
        "당신은 사용자 ACCEPT 직전의 마지막 검토자입니다. 아래 PR을 검토하세요.\n\n"
        "아래 Codex Review Contract(검토 계약)의 11개 항목을 모두 강제 준수하여 검토하세요. "
        "하나라도 위반이 발견되면 'REJECT - <근본 원인>'으로 반려하고, 모든 항목이 충족될 "
        "때에만 'APPROVE_TO_USER'를 출력하세요.\n\n"
        "===== Codex Review Contract 시작 =====\n"
        f"{contract_text}\n"
        "===== Codex Review Contract 끝 =====\n\n"
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
    verdict: str,
    pipeline_id: str,
    pr_url: Optional[str],
    reject_count: int,
) -> Dict[str, Any]:
    """Codex verdict 형식을 검증하고 처리 결과(decision/output/exit_code)를 구성.

    verdict는 정확히 "APPROVE_TO_USER" 또는 "REJECT - <사유>" 형식이어야 한다.
    형식 위반 시 ValueError (fail-closed).
    REJECT: 원문 사유를 단 한 글자도 바꾸지 않고 그대로 output에 담아 exit 2.
            reject_count가 상한(_MAX_REJECT)을 초과하면 루프 중단 안내(exit 0).
    APPROVE_TO_USER: 안내문 + ACCEPT-<pipeline_id> 코드만 출력 (exit 0).

    Args:
        verdict: Codex가 반환한 verdict 텍스트.
        pipeline_id: 파이프라인 ID.
        pr_url: PR 링크 (없으면 None).
        reject_count: 현재까지 누적된 REJECT 횟수 (이번 REJECT 반영 후 값).
    Returns:
        {"decision": "APPROVE"|"REJECT"|"REJECT_HALT",
         "output": str, "exit_code": int, "reject_reason": Optional[str]}.
    Raises:
        TypeError: 인자가 None이거나 타입이 잘못된 경우.
        ValueError: verdict 형식이 위반된 경우, 또는 빈 문자열인 경우.
    """
    if verdict is None:
        raise TypeError("verdict must not be None")
    if not isinstance(verdict, str):
        raise TypeError(f"verdict must be str, got {type(verdict).__name__}")
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(
            f"pipeline_id must be str, got {type(pipeline_id).__name__}"
        )
    if pr_url is not None and not isinstance(pr_url, str):
        raise TypeError(f"pr_url must be str or None, got {type(pr_url).__name__}")
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
        return {
            "decision": "APPROVE",
            "output": _render_approve(pipeline_id, pr_url),
            "exit_code": 0,
            "reject_reason": None,
        }

    if _REJECT_RE.match(normalized):
        # reject_count는 이번 REJECT를 반영한 누적값. 상한 초과 시 루프 중단.
        if reject_count > _MAX_REJECT:
            return {
                "decision": "REJECT_HALT",
                "output": (
                    "Codex Review Loop 자동 중단: 최대 거부 횟수(5) 초과. "
                    "사용자 직접 검토 필요."
                ),
                "exit_code": 0,
                "reject_reason": normalized,
            }
        # REJECT 원문을 단 한 글자도 바꾸지 않고 그대로 재주입 (exit 2)
        return {
            "decision": "REJECT",
            "output": normalized,
            "exit_code": 2,
            "reject_reason": normalized,
        }

    raise ValueError(
        "Codex verdict 형식 위반 — 'APPROVE_TO_USER' 또는 'REJECT - <사유>'만 허용. "
        f"받은 값: {normalized[:80]!r}"
    )


def _project_root() -> Path:
    """프로젝트 루트 디렉토리 경로를 반환.

    PIPELINE_STATE_PATH 환경 변수가 있으면 그 부모를(= .pipeline의 부모와 동일 레벨),
    없으면 cwd를 프로젝트 루트로 사용한다. human_acceptance_packet.md는 이 루트에 위치한다.

    Returns:
        프로젝트 루트 절대 경로.
    """
    env_state = os.environ.get("PIPELINE_STATE_PATH")
    if env_state:
        return Path(env_state).resolve().parent
    return Path.cwd().resolve()


def _human_acceptance_packet_sha256(review_packet: Dict[str, Any]) -> str:
    """human_acceptance_packet.md 파일 내용의 SHA-256을 반환 (gate 비교 기준 SSoT).

    pipeline.py _check_codex_review_gate()는 loop_state["packet_sha256"]을
    acceptance_request.json["packet_sha256"](= human_acceptance_packet.md 파일 SHA)과
    비교한다. 따라서 APPROVE 시 review packet JSON dict가 아니라 동일 기준인
    human_acceptance_packet.md 파일 SHA를 packet_sha256으로 기록해야 한다.

    2차 REJECT 재작업(IMP-20260626-4121): 파일이 없으면 JSON dict SHA로 fallback하던
    fail-soft 동작을 제거한다. fallback이 있으면 _check_codex_review_gate의 packet_sha256
    비교가 무력화되므로, 파일이 반드시 존재해야 하며 없거나 읽기 실패 시 RuntimeError로
    fail-closed 처리한다.

    Args:
        review_packet: build_review_packet 반환 dict (타입 가드용으로만 사용).
    Returns:
        human_acceptance_packet.md 파일 내용의 SHA-256.
    Raises:
        TypeError: review_packet이 None이거나 dict가 아닌 경우.
        RuntimeError: human_acceptance_packet.md 파일이 없거나 읽기 실패 시 (fail-closed).
    """
    if review_packet is None:
        raise TypeError("review_packet must not be None")
    if not isinstance(review_packet, dict):
        raise TypeError(
            f"review_packet must be dict, got {type(review_packet).__name__}"
        )
    packet_path = _project_root() / "human_acceptance_packet.md"
    if packet_path.exists():
        try:
            return _sha256_text(_read_text_with_fallback(packet_path))
        except (OSError, UnicodeDecodeError) as e:
            raise RuntimeError(
                f"human_acceptance_packet.md 읽기 실패 (fail-closed): {e}"
            ) from e
    # fallback 제거 — 파일 없으면 RuntimeError (fail-closed)
    raise RuntimeError(
        "human_acceptance_packet.md 파일이 없습니다. "
        "pipeline.py report final-packet을 먼저 실행하세요 (fail-closed)."
    )


def _project_pipeline_dir() -> Path:
    """프로젝트 루트의 .pipeline 디렉토리 경로를 반환.

    PIPELINE_STATE_PATH 환경 변수가 있으면 그 부모를, 없으면 cwd 기준 .pipeline.

    Returns:
        .pipeline 디렉토리 절대 경로.
    """
    return _project_root() / ".pipeline"


def _get_pr_head_sha(pr_url: str) -> str:
    """gh CLI로 PR head 커밋 SHA를 조회 (fail-closed).

    Args:
        pr_url: PR URL.
    Returns:
        PR head 커밋 SHA.
    Raises:
        TypeError: pr_url이 None이거나 str가 아닌 경우.
        RuntimeError: 조회 실패 (fail-closed).
    """
    if pr_url is None:
        raise TypeError("pr_url must not be None")
    if not isinstance(pr_url, str):
        raise TypeError(f"pr_url must be str, got {type(pr_url).__name__}")
    raw = _run_gh(["pr", "view", pr_url, "--json", "headRefOid"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("gh pr view headRefOid non-JSON — fail-closed") from e
    sha = data.get("headRefOid", "")
    if not sha:
        raise RuntimeError("PR head SHA empty — fail-closed")
    return str(sha)


def _now_iso() -> str:
    """현재 UTC 시각 ISO8601 문자열."""
    return datetime.now(timezone.utc).isoformat()


def _write_hook_log(pipeline_dir: Path, entry: Dict[str, Any]) -> None:
    """hook 실행 로그를 .pipeline/codex_review_loop_hook_log.json에 JSONL 형식으로 추가.

    파일이 있으면 기존 내용에 append, 없으면 새로 생성한다. 파일명은 `.json`이지만
    내용은 한 줄에 하나의 JSON 객체가 쌓이는 JSONL 형식이다.

    Args:
        pipeline_dir: .pipeline 디렉토리 경로.
        entry: 기록할 로그 dict (started_at/pipeline_id/status/failure_code/message 등).
    """
    try:
        log_path = Path(pipeline_dir) / "codex_review_loop_hook_log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(log_path), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[CODEX REVIEW] hook_log 기록 실패 (무시): {e}", file=sys.stderr)


def _record_failed_state(
    state_path: Path, pipeline_id: str, failure_code: str, message: str
) -> None:
    """loop_state에 FAILED 상태를 기록한다. 쓰기 실패는 조용히 무시.

    이미 같은 pipeline_id로 APPROVED인 상태는 FAILED로 덮어쓰지 않는다.

    Args:
        state_path: codex_review_loop_state.json 경로.
        pipeline_id: 현재 파이프라인 ID.
        failure_code: 실패 분류 코드.
        message: 실패 상세 메시지.
    """
    try:
        existing = _load_loop_state(state_path)
        # 기존 APPROVED 상태를 덮어쓰지 않음 — APPROVED이면 FAILED 미기록
        if (
            existing.get("status") == "APPROVED"
            and existing.get("pipeline_id") == pipeline_id
        ):
            return
        _record_state(
            state_path,
            {
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": failure_code,
                "message": message,
                "failed_at": _now_iso(),
            },
        )
    except Exception as e:
        print(f"[CODEX REVIEW] failed_state 기록 실패 (무시): {e}", file=sys.stderr)


def _read_pipeline_id_from_env() -> Optional[str]:
    """acceptance_request.json 또는 active pipeline state에서 pipeline_id를 읽는다.

    transcript 없이 hook이 실행될 때 FAILED 상태를 기록하기 위한 폴백용.

    우선순위:
    1. .pipeline/acceptance_request.json의 pipeline_id 필드
    2. PIPELINE_STATE_PATH 환경변수가 가리키는 state.json의 pipeline_id
    3. .pipeline/active_run.json -> runs/<id>/state.json의 pipeline_id
    4. legacy pipeline_state.json의 active_pipeline_id

    Returns:
        pipeline_id 문자열 또는 None (읽기 실패 시).
    """
    try:
        pipeline_dir = _project_pipeline_dir()

        # 1. acceptance_request.json
        accept_req = pipeline_dir / "acceptance_request.json"
        if accept_req.exists():
            raw = _read_text_with_fallback(accept_req)
            data = json.loads(raw)
            pid = data.get("pipeline_id")
            if pid and isinstance(pid, str):
                return pid

        # 2. PIPELINE_STATE_PATH 환경변수
        env_state = os.environ.get("PIPELINE_STATE_PATH")
        if env_state:
            sp = Path(env_state)
            if sp.exists():
                raw = _read_text_with_fallback(sp)
                data = json.loads(raw)
                pid = data.get("pipeline_id")
                if pid and isinstance(pid, str):
                    return pid

        # 3. active_run.json -> runs/<id>/state.json
        active_ptr = pipeline_dir / "active_run.json"
        if active_ptr.exists():
            raw = _read_text_with_fallback(active_ptr)
            ptr = json.loads(raw)
            state_path_str = ptr.get("state_path")
            if state_path_str:
                sp = Path(state_path_str)
                if sp.exists():
                    raw = _read_text_with_fallback(sp)
                    data = json.loads(raw)
                    pid = data.get("pipeline_id")
                    if pid and isinstance(pid, str):
                        return pid

        # 4. legacy pipeline_state.json
        legacy = _project_root() / "pipeline_state.json"
        if legacy.exists():
            raw = _read_text_with_fallback(legacy)
            data = json.loads(raw)
            pid = data.get("pipeline_id") or data.get("active_pipeline_id")
            if pid and isinstance(pid, str):
                return pid
    except Exception as e:
        print(f"[CODEX REVIEW] pipeline_id 조회 실패 (무시): {e}", file=sys.stderr)
    return None


def _fallback_record_no_transcript(failure_code: str) -> None:
    """last_assistant_message에 5요소 블록이 없을 때 pipeline_id 폴백으로 FAILED 기록.

    pipeline_id를 읽을 수 없으면 조용히 무시한다. (함수명은 하위 호환을 위해 유지)

    Args:
        failure_code: 기록할 실패 코드.
    """
    try:
        pipeline_id = _read_pipeline_id_from_env()
        if not pipeline_id:
            return
        pipeline_dir = _project_pipeline_dir()
        state_path = pipeline_dir / _LOOP_STATE_FILENAME

        # 기존 상태가 현재 pipeline_id와 다를 때만 FAILED 기록
        # (같은 pipeline_id로 이미 APPROVED/REJECTED/FAILED이면 덮어쓰지 않음)
        existing = _load_loop_state(state_path)
        if existing.get("pipeline_id") == pipeline_id:
            # 이미 현재 pipeline_id로 기록된 상태가 있으면 덮어쓰지 않음
            return

        _record_state(
            state_path,
            {
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": failure_code,
                "message": (
                    f"hook 입력에 5요소 승인 요청 블록이 없음 ({failure_code}). "
                    "last_assistant_message에서 5요소를 감지할 수 없습니다."
                ),
                "failed_at": _now_iso(),
            },
        )
        _write_hook_log(
            pipeline_dir,
            {
                "started_at": _now_iso(),
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": failure_code,
                "message": "5요소 블록 없음",
            },
        )
    except Exception as e:
        print(f"[CODEX REVIEW] fallback 기록 실패 (무시): {e}", file=sys.stderr)


def main(hook_data_override: Optional[Dict[str, Any]] = None) -> int:
    """hook 진입점. stdin JSON의 last_assistant_message에서 승인 블록 탐지 후 Codex 검토.

    Claude Code Stop hook은 hook 데이터를 stdin JSON으로 전달하며, 그 안의
    last_assistant_message 필드에 마지막 assistant 메시지가 들어 있다. transcript 파일
    경로를 파싱할 필요 없이 이 필드를 바로 5요소 패턴 감지에 사용한다.

    Args:
        hook_data_override: 테스트용 hook_data 주입. None이면 stdin에서 읽음.
    Returns:
        프로세스 종료 코드.
        - 0: 블록 없음/중복 차단/APPROVE/REJECT 상한 중단 (정상)
        - 2: REJECT 재주입 (Stop hook 재주입)
        - 1: fail-closed 오류
    """
    if hook_data_override is not None:
        if not isinstance(hook_data_override, dict):
            raise TypeError(
                "hook_data_override must be dict, got "
                f"{type(hook_data_override).__name__}"
            )
        hook_data = hook_data_override
    else:
        try:
            raw = sys.stdin.read()
            hook_data = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, OSError):
            hook_data = {}

    # last_assistant_message에서 직접 5요소 패턴 감지
    last_message = hook_data.get("last_assistant_message", "") or ""
    if not isinstance(last_message, str):
        last_message = ""

    # IMP-20260627-3907 hotfix-7: 중복 trigger block 감지. 같은 메시지에 사용자 승인
    # 요청이 2회 이상이면 delivery path 설계 결함(중복 안내)이므로 FAILED 기록 후 차단.
    duplicate_count = last_message.count("사용자 승인 요청")
    if duplicate_count >= 2:
        pipeline_id_fallback = _read_pipeline_id_from_env()
        if pipeline_id_fallback:
            pipeline_dir_fb = _project_pipeline_dir()
            state_path_fb = pipeline_dir_fb / _LOOP_STATE_FILENAME
            _record_failed_state(
                state_path_fb,
                pipeline_id_fallback,
                "duplicate_trigger_block",
                f"last_assistant_message에 '사용자 승인 요청'이 {duplicate_count}회 감지됨. "
                "단일 trigger block만 허용합니다.",
            )
            _write_hook_log(
                pipeline_dir_fb,
                {
                    "started_at": _now_iso(),
                    "pipeline_id": pipeline_id_fallback,
                    "status": "FAILED",
                    "failure_code": "duplicate_trigger_block",
                    "message": f"중복 trigger block {duplicate_count}회",
                },
            )
        return 0

    block = parse_acceptance_block(last_message)
    if block is None:
        # 5요소 없음 — fallback: pipeline_id로 FAILED 기록 시도 후 종료
        _fallback_record_no_transcript("no_five_element_block")
        return 0

    pr_url = block["pr_url"]
    pipeline_id = block["pipeline_id"]

    pipeline_dir = _project_pipeline_dir()
    state_path = pipeline_dir / _LOOP_STATE_FILENAME

    # hook 진입 즉시 RUNNING 로그 (5요소 감지 직후 관찰성 확보)
    _write_hook_log(
        pipeline_dir,
        {
            "started_at": _now_iso(),
            "pipeline_id": pipeline_id,
            "status": "RUNNING",
        },
    )

    # PROCESSING 기록 전에 직전 상태를 먼저 읽는다 (중복 PROCESSING 판정 및
    # stale APPROVED 판정에는 이번 hook이 쓰기 전의 상태가 필요하기 때문이다).
    prior_state = _load_loop_state(state_path)
    prior_status = prior_state.get("status", "")
    prior_pid = prior_state.get("pipeline_id", "")

    # 같은 pipeline_id로 이미 PROCESSING 중이면 중복 실행 방지.
    # 단, 5분(300초) 이상 지속된 PROCESSING은 STALE_PROCESSING으로 전환 후 재시도 허용.
    if prior_status == "PROCESSING" and prior_pid == pipeline_id:
        started_at_str = prior_state.get("started_at", "")
        stale = False
        if started_at_str:
            try:
                started_at = datetime.fromisoformat(started_at_str)
                elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
                if elapsed > 300:
                    stale = True
            except (ValueError, TypeError):
                stale = True  # 파싱 실패 시 stale로 처리
        if not stale:
            # 5분 미만 PROCESSING: 중복 실행 차단
            return 0
        # 5분 초과: STALE_PROCESSING으로 전환 후 재시도 허용
        try:
            _record_state(
                state_path,
                {
                    "pipeline_id": pipeline_id,
                    "status": "STALE_PROCESSING",
                    "pr_url": pr_url,
                    "stale_at": _now_iso(),
                    "original_started_at": started_at_str,
                },
            )
        except Exception as e:
            print(
                f"[CODEX REVIEW] STALE_PROCESSING 기록 실패 (무시): {e}",
                file=sys.stderr,
            )

    # 5요소 감지 즉시 pipeline_id 기준으로 PROCESSING 상태 기록 (관찰성). 이전
    # 파이프라인의 APPROVED/REJECTED 상태가 남아있더라도 현재 pipeline_id로 덮어쓴다.
    try:
        _record_state(
            state_path,
            {
                "pipeline_id": pipeline_id,
                "status": "PROCESSING",
                "pr_url": pr_url,
                "started_at": _now_iso(),
            },
        )
    except Exception as e:
        print(f"[CODEX REVIEW] PROCESSING 기록 실패: {e}", file=sys.stderr)
        _record_failed_state(state_path, pipeline_id, "processing_record_failed", str(e))

    # --- fail-closed 영역 시작 ---
    try:
        head_sha = _get_pr_head_sha(pr_url)
    except RuntimeError as e:
        _record_failed_state(state_path, pipeline_id, "pr_head_sha_failed", str(e))
        _write_hook_log(
            pipeline_dir,
            {
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": "pr_head_sha_failed",
                "message": str(e),
            },
        )
        print(
            f"[CODEX REVIEW] PR head SHA 조회 실패 (fail-closed): {e}",
            file=sys.stderr,
        )
        return 1

    # PROCESSING으로 덮어쓰기 전의 직전 상태를 사용한다 (reject_count, 기존
    # APPROVED/REJECTED 이력이 PROCESSING 기록으로 사라지지 않도록 prior_state 재사용).
    loop_state = prior_state

    # packet 수집은 codex 호출과 SHA 산출 양쪽에 필요. 단, APPROVED 재트리거 방지
    # 판정에는 기존 packet_sha256과 비교가 필요하므로 packet을 먼저 수집한다.
    try:
        packet = build_review_packet(pr_url, block["accept_code"], head_sha)
    except (RuntimeError, TypeError, ValueError) as e:
        _record_failed_state(
            state_path, pipeline_id, "packet_collection_failed", str(e)
        )
        _write_hook_log(
            pipeline_dir,
            {
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": "packet_collection_failed",
                "message": str(e),
            },
        )
        print(
            f"[CODEX REVIEW] PR packet 수집 실패 (fail-closed): {e}",
            file=sys.stderr,
        )
        return 1

    # review_packet_sha256: gh로 수집한 review packet JSON dict의 SHA (검토 내용 변경 추적용).
    review_packet_sha256 = _sha256_text(
        json.dumps(packet, ensure_ascii=False, sort_keys=True)
    )
    # packet_sha256: human_acceptance_packet.md 파일 SHA (gate 비교 기준 SSoT). 결함 1 수정:
    # _check_codex_review_gate()가 acceptance_request.json["packet_sha256"]과 비교하므로
    # 동일 기준(human_acceptance_packet.md 파일 SHA)으로 산출해야 stale 오탐을 방지한다.
    # 2차 REJECT 재작업: 파일 미존재 시 RuntimeError(fail-closed)이므로 여기서 처리한다.
    try:
        packet_sha256 = _human_acceptance_packet_sha256(packet)
    except RuntimeError as e:
        _record_failed_state(state_path, pipeline_id, "packet_sha256_failed", str(e))
        _write_hook_log(
            pipeline_dir,
            {
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": "packet_sha256_failed",
                "message": str(e),
            },
        )
        print(
            f"[CODEX REVIEW] acceptance packet SHA 계산 실패 (fail-closed): {e}",
            file=sys.stderr,
        )
        return 1

    # 이미 같은 head/packet/pipeline_id로 APPROVED면 Codex CLI 재호출 없이 같은
    # APPROVE 출력만. pipeline_id를 함께 전달하여 다른 파이프라인의 APPROVED를
    # stale로 오판하지 않도록 한다.
    if _check_stale(loop_state, head_sha, packet_sha256, pipeline_id):
        print(_render_approve(pipeline_id, pr_url))
        return 0

    try:
        prompt = _build_codex_prompt(packet)
        verdict = call_codex_cli(prompt)
    except (RuntimeError, TypeError, ValueError) as e:
        _record_failed_state(state_path, pipeline_id, "codex_call_failed", str(e))
        _write_hook_log(
            pipeline_dir,
            {
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": "codex_call_failed",
                "message": str(e),
            },
        )
        print(
            f"[CODEX REVIEW] Codex 호출 실패 (fail-closed): {e}", file=sys.stderr
        )
        return 1

    # verdict 형식만 먼저 판별하여 REJECT 사유를 확보 (중복 판정에 필요)
    normalized = verdict.strip()
    is_reject = bool(_REJECT_RE.match(normalized))

    # REJECT 중복 주입 차단: 같은 pipeline/head/packet/reason 반복이면 조용히 exit 0
    if is_reject and _is_duplicate_reject(
        loop_state, pipeline_id, head_sha, packet_sha256, normalized
    ):
        return 0

    # 누적 REJECT 횟수 산정 (이번 REJECT 반영)
    prev_reject_count = loop_state.get("reject_count", 0)
    if not isinstance(prev_reject_count, int) or isinstance(prev_reject_count, bool):
        prev_reject_count = 0
    reject_count = prev_reject_count + 1 if is_reject else prev_reject_count

    try:
        outcome = process_verdict(verdict, pipeline_id, pr_url, reject_count)
    except (TypeError, ValueError) as e:
        _record_failed_state(
            state_path, pipeline_id, "verdict_format_invalid", str(e)
        )
        _write_hook_log(
            pipeline_dir,
            {
                "pipeline_id": pipeline_id,
                "status": "FAILED",
                "failure_code": "verdict_format_invalid",
                "message": str(e),
            },
        )
        print(
            f"[CODEX REVIEW] Codex 응답 형식 위반 (fail-closed): {e}",
            file=sys.stderr,
        )
        return 1

    decision = outcome["decision"]
    if decision in ("REJECT", "REJECT_HALT"):
        _record_state(
            state_path,
            {
                "pipeline_id": pipeline_id,
                "status": "REJECTED",
                "pr_head_sha": head_sha,
                "packet_sha256": packet_sha256,
                "review_packet_sha256": review_packet_sha256,
                "last_reject_reason": outcome["reject_reason"],
                "reject_count": reject_count,
                "last_checked_at": _now_iso(),
            },
        )
    else:  # APPROVE
        _record_state(
            state_path,
            {
                "pipeline_id": pipeline_id,
                "status": "APPROVED",
                "pr_head_sha": head_sha,
                "pr_body_sha256": _sha256_text(str(packet.get("body", ""))),
                "packet_sha256": packet_sha256,
                "review_packet_sha256": review_packet_sha256,
                "accept_code": f"ACCEPT-{pipeline_id}",
                "approved_at": _now_iso(),
            },
        )

    # REJECT 원문은 prefix/suffix 없이 그대로, APPROVE는 안내 메시지 출력
    print(outcome["output"])
    return int(outcome["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
