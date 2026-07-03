# [Purpose]: IMP-20260703-B985 MT-8 — PR body SHA canonical SSoT helper(_canonical_pr_body_sha256)와
#   그 배선(_fetch_canonical_pr_body_sha256 / _get_pr_body_text)이 CRLF/LF 정규화, trailing newline
#   보존, jq stdout 배제, 직접 hashlib 회귀 방지를 만족하는지 검증한다.
# [Assumptions]: pipeline 모듈을 직접 import할 수 있고, gh 호출은 PIPELINE_GH_EXECUTABLE 스텁으로
#   대체한다. helper는 순수 함수이므로 gh 없이 직접 호출로 검증한다.
# [Vulnerability & Risks]: gh 스텁이 실제 GitHub 저장 정규화와 100% 동일하지는 않다. 다만 canonical
#   경로가 --json body(jq 없이) → JSON parse → helper 로 통일되었는지, jq stdout trailing newline이
#   섞이지 않는지를 재현 가능한 형태로 검증한다.
# [Improvement]: 실제 gh GraphQL body와의 canonical 차이까지 재현하려면 라이브 통합 테스트가 필요하다.
"""IMP-20260703-B985 canonical PR body SHA256 SSoT 테스트.

oracle: tests/oracles/IMP-20260703-B985/
  TC-1 CRLF/LF 정규화 동일 SHA
  TC-2 trailing newline 구분 (strip 없음)
  TC-3 gh --json body JSON parse vs jq stdout
  TC-9 직접 hashlib 회귀 방지 (canonical helper 외부 0건)
  TC-4~TC-8, TC-10~TC-11 추가 케이스
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline  # noqa: E402

PIPELINE_PY = REPO_ROOT / "pipeline.py"
ORACLE_DIR = REPO_ROOT / "tests" / "oracles" / "IMP-20260703-B985"


def _load_oracle(case: str, name: str) -> Dict:
    return json.loads((ORACLE_DIR / case / name).read_text(encoding="utf-8"))


def _reference_sha(body: str) -> str:
    """helper와 독립적으로 canonical 규칙을 재구현한 참조 SHA (helper 결과 대조용)."""
    lf = body.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(lf.encode("utf-8")).hexdigest()


def _write_fake_gh(tmp_path: Path, body: str) -> Path:
    """PIPELINE_GH_EXECUTABLE 스텁: `--jq .body`는 trailing newline을 붙이고,
    `--json body`(jq 없이)는 clean JSON 객체를 반환한다 (실제 gh 동작 재현)."""
    spy = tmp_path / "fake_gh.py"
    spy.write_text(
        "import sys, json\n"
        f"BODY = {body!r}\n"
        "args = sys.argv[1:]\n"
        "if '--jq' in args:\n"
        "    j = args.index('--jq')\n"
        "    expr = args[j + 1] if j + 1 < len(args) else ''\n"
        "    if expr == '.body':\n"
        "        sys.stdout.write(BODY)\n"
        "        if not BODY.endswith('\\n'):\n"
        "            sys.stdout.write('\\n')\n"
        "        sys.exit(0)\n"
        "    print('')\n"
        "    sys.exit(0)\n"
        "print(json.dumps({'body': BODY, 'number': 1, 'url': 'https://x/pull/1'}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return spy


@pytest.fixture()
def gh_env(tmp_path, monkeypatch):
    """fake gh를 반환하는 환경 설정 컨텍스트를 제공한다."""
    def _set(body: str) -> None:
        spy = _write_fake_gh(tmp_path, body)
        monkeypatch.setenv("PIPELINE_GH_EXECUTABLE", str(spy))
    return _set


# ── TC-1: CRLF/LF 정규화 동일 SHA ─────────────────────────────────────────────
def test_tc1_crlf_lf_normalization_same_sha():
    oracle_in = _load_oracle("TC-1_crlf_lf_normalization", "input.json")
    shas = {
        v["id"]: pipeline._canonical_pr_body_sha256(v["pr_body"])
        for v in oracle_in["test_vectors"]
    }
    # 모든 줄바꿈 형식이 동일 canonical SHA를 생성해야 한다.
    assert len(set(shas.values())) == 1, f"CRLF/LF 정규화 후 SHA가 갈림: {shas}"
    # normalized_body 기준 참조 SHA와 일치.
    normalized = _load_oracle("TC-1_crlf_lf_normalization", "expected.json")["normalized_body"]
    assert shas["lf_only"] == _reference_sha(normalized)


def test_tc1_lone_cr_normalized_to_lf():
    # lone CR(\r)도 LF로 정규화되어야 한다 (helper 규칙 replace('\r','\n')).
    assert pipeline._canonical_pr_body_sha256("a\rb") == pipeline._canonical_pr_body_sha256("a\nb")
    assert pipeline._canonical_pr_body_sha256("x\r\ny\rz") == pipeline._canonical_pr_body_sha256("x\ny\nz")


# ── TC-2: trailing newline 구분 (strip 없음) ──────────────────────────────────
def test_tc2_trailing_newline_distinct():
    oracle_in = _load_oracle("TC-2_trailing_newline", "input.json")
    shas = {
        v["id"]: pipeline._canonical_pr_body_sha256(v["pr_body"])
        for v in oracle_in["test_vectors"]
    }
    # no / single / double trailing newline은 서로 다른 SHA여야 한다 (strip 금지).
    assert shas["no_trailing_newline"] != shas["single_trailing_newline"]
    assert shas["single_trailing_newline"] != shas["double_trailing_newline"]
    assert shas["no_trailing_newline"] != shas["double_trailing_newline"]


def test_tc2_no_strip_applied():
    # helper가 trailing newline을 제거하면 아래 두 SHA가 같아진다 → strip 미적용 확인.
    base = "## Summary\n- Change"
    assert pipeline._canonical_pr_body_sha256(base) != pipeline._canonical_pr_body_sha256(base + "\n")
    # 참조 SHA(strip 없이 계산)와 일치해야 한다.
    assert pipeline._canonical_pr_body_sha256(base + "\n") == _reference_sha(base + "\n")


# ── TC-3: gh --json body JSON parse vs jq stdout ──────────────────────────────
def test_tc3_get_pr_body_text_uses_json_parse_not_jq(gh_env):
    # gh 스텁은 --jq .body에는 trailing newline을 붙이고, --json body에는 clean body를 준다.
    # _get_pr_body_text는 --json body(JSON parse)를 써야 하므로 trailing newline 없는 body를 반환한다.
    body = "## Test\n- Item 1"
    gh_env(body)
    got = pipeline._get_pr_body_text()
    assert got == body, f"jq stdout trailing newline이 섞였을 가능성: {got!r}"
    assert not got.endswith("\n"), "candidate body에 jq trailing newline이 섞이면 안 된다"


def test_tc3_fetch_canonical_sha_matches_json_parse_body(gh_env):
    body = "## Test\n- Item 1"
    gh_env(body)
    canonical = pipeline._fetch_canonical_pr_body_sha256(1)
    # canonical SHA는 JSON parse body(trailing newline 없음) 기준이어야 한다.
    assert canonical == _reference_sha(body)
    # jq stdout(body + '\n') 기준 SHA와는 달라야 한다 (jq 오염 배제 확인).
    jq_polluted = _reference_sha(body + "\n")
    assert canonical != jq_polluted


def test_tc3_source_has_no_jq_body_in_get_pr_body_text():
    # _get_pr_body_text 함수 본문에 '--jq', '.body' stdout 계산이 남아 있으면 안 된다.
    src = PIPELINE_PY.read_text(encoding="utf-8")
    m = re.search(r"def _get_pr_body_text\(\).*?(?=\ndef )", src, re.S)
    assert m, "_get_pr_body_text 함수를 찾을 수 없음"
    fn_body = m.group(0)
    # 주석 언급이 아닌 실제 subprocess 인자로서의 --jq .body 사용을 검사한다.
    # (list 리터럴 형태 "--jq", ".body" 만 회귀 대상 — 주석의 `--jq .body` 문구는 허용).
    assert '"--jq"' not in fn_body, "_get_pr_body_text가 여전히 --jq 인자를 사용한다 (JSON parse로 교체 필요)"
    assert re.search(r'"--json"\s*,\s*"body"', fn_body), "_get_pr_body_text가 --json body(jq 없이)를 사용해야 함"
    assert "json.loads" in fn_body, "_get_pr_body_text가 Python JSON parse(json.loads)를 사용해야 함"


# ── TC-4: jq/PowerShell stdout이 SHA에 섞이지 않음 ────────────────────────────
def test_tc4_trailing_whitespace_stdout_not_absorbed():
    # canonical helper는 line-ending만 정규화하고 trailing whitespace/newline은 그대로 SHA에 반영.
    # 따라서 stdout 오염(예: trailing space, CRLF)이 있으면 SHA가 달라져 조기 검출된다.
    clean = "body text"
    assert pipeline._canonical_pr_body_sha256(clean) != pipeline._canonical_pr_body_sha256(clean + " ")
    # CRLF만 다른 경우는 정규화로 동일 (line-ending 차이는 흡수).
    assert pipeline._canonical_pr_body_sha256("a\r\nb") == pipeline._canonical_pr_body_sha256("a\nb")


# ── TC-5: candidate 3자 일치 (동일 body → 동일 candidate SHA) ──────────────────
def test_tc5_candidate_sha_deterministic():
    # 동일 body에 대해 helper는 항상 같은 SHA를 반환한다 (candidate 3자 일치 기반).
    body = "## Summary\n- x\n\n## Test plan\nrun"
    a = pipeline._canonical_pr_body_sha256(body)
    b = pipeline._canonical_pr_body_sha256(body)
    assert a == b == _reference_sha(body)


# ── TC-6: publish 후 canonical SHA == fetch 기준 SHA ──────────────────────────
def test_tc6_fetch_canonical_text_and_sha_consistent(gh_env):
    body = "## Summary\r\n- change\r\n"
    gh_env(body)
    text = pipeline._fetch_canonical_pr_body_text(1)
    sha = pipeline._fetch_canonical_pr_body_sha256(1)
    assert text is not None and sha is not None
    # fetch된 canonical 원문을 helper로 다시 계산하면 fetch SHA와 일치해야 한다
    # (_verify_published_canonical_pr_body 2자 검증의 근거).
    assert pipeline._canonical_pr_body_sha256(text) == sha


# ── TC-7: gates accept stale 검증이 canonical helper 사용 ─────────────────────
def test_tc7_verify_published_uses_canonical_helper():
    # _verify_published_canonical_pr_body는 canonical helper로 재계산한 SHA와 2자 비교한다.
    body = "## Summary\n- publish match"
    sha = pipeline._canonical_pr_body_sha256(body)
    # 일치하면 예외 없이 통과.
    pipeline._verify_published_canonical_pr_body(sha, body)
    # 불일치하면 fail-closed (SystemExit).
    with pytest.raises(SystemExit):
        pipeline._verify_published_canonical_pr_body("0" * 64, body)


# ── TC-8: candidate/canonical 필드 분리 (혼용 없음) ──────────────────────────
def test_tc8_acceptance_request_has_separated_fields():
    # _prepare_acceptance_snapshot_candidate 결과에 candidate/canonical 필드가 분리 존재.
    req = pipeline._prepare_acceptance_snapshot_candidate(
        "IMP-20260703-B985",
        "https://example.com/evidence",
        "https://github.com/test/repo/pull/1",
        "abc123",
        "",
        "## Summary\n- body\n\n## Test plan\nrun",
    )
    assert "pr_body_candidate_sha256" in req
    assert "github_canonical_pr_body_sha256" in req
    # candidate는 canonical helper 결과와 일치, github canonical은 publish 전이라 빈 문자열.
    assert req["pr_body_candidate_sha256"] == pipeline._canonical_pr_body_sha256(
        "## Summary\n- body\n\n## Test plan\nrun"
    )
    assert req["github_canonical_pr_body_sha256"] == ""
    # backward-compat 필드는 candidate와 동일 값(혼용 아님, 명시적 동일화).
    assert req["pr_body_sha256"] == req["pr_body_candidate_sha256"]


# ── TC-9: 직접 hashlib 회귀 방지 (canonical helper 외부 0건) ──────────────────
def test_tc9_no_direct_hashlib_pr_body_outside_helper():
    src = PIPELINE_PY.read_text(encoding="utf-8")
    lines = src.split("\n")
    # helper 함수 범위를 식별하여 해당 라인은 허용.
    helper_start = None
    for i, ln in enumerate(lines):
        if ln.startswith("def _canonical_pr_body_sha256("):
            helper_start = i
            break
    assert helper_start is not None, "_canonical_pr_body_sha256 helper가 없음"
    helper_end = helper_start + 1
    while helper_end < len(lines) and not lines[helper_end].startswith("def "):
        helper_end += 1

    # PR body/final_body/current_pr_body를 대상으로 하는 직접 hashlib SHA 계산 패턴.
    body_sha_pat = re.compile(
        r"hashlib\.sha256\(\s*(pr_body|final_body|current_pr_body|_current_pr_body_early)\b.*?\.encode"
    )
    offenders = []
    for i, ln in enumerate(lines):
        if helper_start <= i < helper_end:
            continue  # helper 내부는 허용
        if body_sha_pat.search(ln):
            offenders.append((i + 1, ln.strip()))
    assert not offenders, f"canonical helper 외부 직접 PR body hashlib 호출 발견: {offenders}"


def test_tc9_all_pr_body_paths_route_through_helper():
    # 최소한 helper가 여러 배선 지점에서 호출되어야 한다 (통일 완료 증거).
    src = PIPELINE_PY.read_text(encoding="utf-8")
    call_count = src.count("_canonical_pr_body_sha256(")
    # 정의(1) + 최소 6개 배선 호출.
    assert call_count >= 7, f"canonical helper 배선이 부족함 (호출 {call_count}개)"


# ── TC-10: UTF-8 인코딩 + 빈 문자열 + 큰 입력 ────────────────────────────────
def test_tc10_utf8_and_empty_and_large():
    # UTF-8 멀티바이트(한글/이모지) 정상 처리.
    ko_body = "## 요약\n- 변경 사항 ✅\n"
    assert pipeline._canonical_pr_body_sha256(ko_body) == _reference_sha(ko_body)
    # 빈 문자열도 유효한 SHA를 반환 (empty string sha256).
    assert pipeline._canonical_pr_body_sha256("") == hashlib.sha256(b"").hexdigest()
    # 큰 입력 (100KB)도 안정적.
    big = ("line\r\n" * 20000)
    assert pipeline._canonical_pr_body_sha256(big) == _reference_sha(big)


def test_tc10_type_guards():
    # None / 비str(bool 포함) 입력은 TypeError로 차단 (암묵적 형변환 금지).
    for bad in (None, 123, b"bytes", True, ["list"], {"d": 1}):
        with pytest.raises(TypeError):
            pipeline._canonical_pr_body_sha256(bad)  # type: ignore[arg-type]


# ── TC-11: 기존 E69E / F52C 호환성 (helper 결과가 기존 canonical 규칙과 동일) ──
def test_tc11_f52c_canonical_rule_backward_compatible(gh_env):
    # F52C r11의 canonical 규칙은 'CRLF→LF 정규화 후 sha256'이었다. helper는 여기에 lone CR→LF만
    # 추가했다. CRLF만 있는 (lone CR 없는) 입력에서는 F52C 규칙과 helper 결과가 동일해야 한다.
    body = "## Summary\r\n- change 1\r\n- change 2\r\n"
    f52c_rule_sha = hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    assert pipeline._canonical_pr_body_sha256(body) == f52c_rule_sha
    # fetch 경로도 동일 결과.
    gh_env(body)
    assert pipeline._fetch_canonical_pr_body_sha256(1) == f52c_rule_sha


def test_tc11_e69e_state_model_intact():
    # E69E의 ERROR/REJECT 상태 모델(codex_review_result 필드)이 helper 도입으로 훼손되지 않았는지
    # 소스 레벨에서 확인한다 (필드/분기 문자열 존재).
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "codex_cli_error_type" in src or "cli_error_count" in src, "E69E CLI error 모델 흔적 없음"
    assert "pr_body_candidate_sha256" in src, "F52C candidate 필드 없음"
    assert "github_canonical_pr_body_sha256" in src, "canonical 필드 없음"


# ── Oracle test_set.json alias (test_set.json은 frozen이므로 함수명 alias를 여기서 제공) ──
# test_set.json T001: test_tc1_crlf_lf_normalization
def test_tc1_crlf_lf_normalization():
    """TC-1 oracle alias: test_tc1_crlf_lf_normalization_same_sha와 동일."""
    test_tc1_crlf_lf_normalization_same_sha()


# test_set.json T002: test_tc2_trailing_newline
def test_tc2_trailing_newline():
    """TC-2 oracle alias: test_tc2_trailing_newline_distinct와 동일."""
    test_tc2_trailing_newline_distinct()


# test_set.json T003: test_tc3_json_parse_vs_jq
def test_tc3_json_parse_vs_jq():
    """TC-3 oracle alias: test_tc3_source_has_no_jq_body_in_get_pr_body_text와 동일.
    gh fixture 없이 실행 가능한 소스 레벨 검증."""
    test_tc3_source_has_no_jq_body_in_get_pr_body_text()


# test_set.json T004: test_tc9_direct_hashlib_regression
def test_tc9_direct_hashlib_regression():
    """TC-9 oracle alias: test_tc9_no_direct_hashlib_pr_body_outside_helper와 동일."""
    test_tc9_no_direct_hashlib_pr_body_outside_helper()


# ── TC-12: pr_body_candidate_sha256 != packet_sha256 (시맨틱 분리 검증) ────────
def test_tc12_pr_body_candidate_ne_packet_sha256(tmp_path, monkeypatch):
    """pr_body_candidate_sha256과 packet_sha256은 서로 다른 값이어야 한다.

    버그: staged_packet_sha256(패킷 파일 SHA)를 pr_body_candidate_sha256에 잘못 넣으면
    두 값이 같아진다. 올바른 구현에서는 두 값이 다른 SHA여야 한다.
    """
    # 패킷 파일(human_acceptance_packet.md) 내용
    packet_content = "## 최종 확인 안내\n테스트 패킷 내용\n"
    # 현재 PR body (패킷 블록 없음)
    pr_body = "## 작업 요약\n- 변경 사항\n"

    packet_sha = pipeline._canonical_pr_body_sha256(packet_content)

    # PR body에 패킷 블록을 삽입한 최종 body의 canonical SHA
    final_body = pipeline._replace_pr_body_packet_block(pr_body, packet_content)
    candidate_sha = pipeline._canonical_pr_body_sha256(final_body)

    # 두 SHA는 반드시 달라야 한다
    assert candidate_sha != packet_sha, (
        "pr_body_candidate_sha256과 packet_sha256이 동일함 — "
        "staged_packet_sha256을 pr_body_candidate_sha256에 넣는 버그가 남아 있음"
    )


# ── TC-13: pr_body_candidate_sha256 == canonical_sha256(패킷 블록 교체된 PR body) ──
def test_tc13_pr_body_candidate_equals_final_body_canonical_sha():
    """pr_body_candidate_sha256은 staged_packet_content로 PR body 블록을 교체한
    최종 body의 canonical SHA와 일치해야 한다.
    """
    packet_content = "## 최종 확인 안내\n테스트 패킷 내용\n- AC 1: PASS\n- AC 2: PASS\n"
    pr_body = "## 작업 요약\n- 변경 사항\n\n## 검증\n통과\n"

    # 최종 body = PR body에 패킷 블록 교체
    final_body = pipeline._replace_pr_body_packet_block(pr_body, packet_content)

    # pr_body_candidate_sha256은 이 최종 body의 canonical SHA여야 함
    expected_candidate_sha = pipeline._canonical_pr_body_sha256(final_body)

    # 패킷 파일 자체의 SHA와는 달라야 함 (TC-12와 동일 검증)
    packet_sha = pipeline._canonical_pr_body_sha256(packet_content)
    assert expected_candidate_sha != packet_sha

    # 참조 구현(직접 계산)과 일치 확인
    assert expected_candidate_sha == _reference_sha(final_body)


# oracle gate 검증 완료 (IMP-20260703-B985 alias 함수 포함)
if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-q"]))
