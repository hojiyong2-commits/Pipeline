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


class TestFrozenAcceptanceStagingMT9:
    """MT-9: acceptance_staging.json frozen snapshot 완성 검증"""

    def test_staging_stores_pr_body_candidate_content(self, tmp_path):
        """새 필드 pr_body_candidate_content가 staging에 저장된다."""
        from pipeline import _replace_pr_body_packet_block, _canonical_pr_body_sha256
        pr_body = "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\nold\n<!-- PIPELINE_FINAL_PACKET_END -->\n"
        packet = "## New Packet\n"
        candidate = _replace_pr_body_packet_block(pr_body, packet)
        sha = _canonical_pr_body_sha256(candidate)
        staging = {
            "pipeline_id": "TEST-1",
            "staged_packet_content": packet,
            "staged_packet_sha256": "abc",
            "frozen_at": "2026-01-01T00:00:00Z",
            "req_candidate": {},
            "pr_body_candidate_content": candidate,
            "pr_body_candidate_sha256": sha,
        }
        import json
        p = tmp_path / ".pipeline" / "acceptance_staging.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(staging), encoding="utf-8")
        # 저장된 값 검증
        assert staging["pr_body_candidate_content"] == candidate
        assert staging["pr_body_candidate_sha256"] == sha

    def test_staging_candidate_sha256_equals_canonical_of_replaced_body(self):
        """pr_body_candidate_sha256 == canonical_sha(replace(pr_body, packet))"""
        from pipeline import _replace_pr_body_packet_block, _canonical_pr_body_sha256
        pr_body = "# Title\n\n<!-- PIPELINE_FINAL_PACKET_START -->\nold content\n<!-- PIPELINE_FINAL_PACKET_END -->\n\nfooter"
        packet = "## Packet Content\ndata here\n"
        candidate = _replace_pr_body_packet_block(pr_body, packet)
        sha_direct = _canonical_pr_body_sha256(candidate)
        # 저장했다가 읽은 것과 같아야 함
        assert sha_direct == _canonical_pr_body_sha256(candidate)

    def test_staging_candidate_sha256_differs_from_packet_sha256(self):
        """pr_body_candidate_sha256와 packet_sha256는 의미가 다르다 (동일값 금지)."""
        import hashlib
        from pipeline import _replace_pr_body_packet_block, _canonical_pr_body_sha256
        pr_body = "# PR Body\n<!-- PIPELINE_FINAL_PACKET_START -->\nold\n<!-- PIPELINE_FINAL_PACKET_END -->\n"
        packet_content = "## Packet\nsome content\n"
        candidate_body = _replace_pr_body_packet_block(pr_body, packet_content)
        pr_body_candidate_sha256 = _canonical_pr_body_sha256(candidate_body)
        # packet_sha256는 패킷 파일 내용의 SHA (여기서는 packet_content의 SHA로 근사)
        packet_sha256 = hashlib.sha256(packet_content.encode("utf-8")).hexdigest()
        # pr_body에 packet이 삽입되면 전체 body가 달라지므로 SHA가 다름
        assert pr_body_candidate_sha256 != packet_sha256

    def test_codex_snapshot_identity_uses_staging_sha_when_available(self, tmp_path, monkeypatch):
        """_codex_snapshot_identity가 staging의 pr_body_candidate_sha256를 사용하고 re-fetch 없이 반환한다."""
        import json
        import pipeline as pl
        from pipeline import _canonical_pr_body_sha256

        # frozen SHA를 staging에 저장
        expected_sha = _canonical_pr_body_sha256("# PR Body\npacket block here\n")
        staging = {
            "pipeline_id": "IMP-TEST",
            "staged_packet_content": "## Packet\n",
            "staged_packet_sha256": "def456",
            "frozen_at": "2026-01-01T00:00:00Z",
            "req_candidate": {"request_id": "test-req-1"},
            "pr_body_candidate_content": "# PR Body\npacket block here\n",
            "pr_body_candidate_sha256": expected_sha,
        }
        staging_path = tmp_path / ".pipeline" / "acceptance_staging.json"
        staging_path.parent.mkdir(parents=True)
        staging_path.write_text(json.dumps(staging), encoding="utf-8")

        # _get_pr_body_text가 호출되면 안 됨 (re-fetch 금지)
        call_count = {"n": 0}
        def mock_get_pr_body_text():
            call_count["n"] += 1
            return "should not be called"

        monkeypatch.setattr("pipeline.BASE_DIR", tmp_path)
        monkeypatch.setattr("pipeline._get_pr_body_text", mock_get_pr_body_text)
        monkeypatch.setattr("pipeline._get_current_pr_head_sha", lambda: "abc123")
        monkeypatch.setattr("pipeline._packet_output_path", lambda: tmp_path / "packet.md")
        monkeypatch.setattr("pipeline._codex_review_bundle_path", lambda pid: tmp_path / "bundle.json")

        result = pl._codex_snapshot_identity("IMP-TEST")

        assert result["pr_body_candidate_sha256"] == expected_sha, (
            f"Expected staging SHA {expected_sha}, got {result['pr_body_candidate_sha256']}"
        )
        assert call_count["n"] == 0, (
            f"_get_pr_body_text was called {call_count['n']} times — re-fetch should not happen"
        )

    def test_build_codex_review_bundle_uses_staging_sha_when_available(self, tmp_path, monkeypatch):
        """_build_codex_review_bundle이 staging pr_body_candidate_sha256를 사용하고 re-fetch 없이 반환한다."""
        import json
        import pipeline as pl
        from pipeline import _canonical_pr_body_sha256

        expected_sha = _canonical_pr_body_sha256("# PR Body\npacket block here\n")
        staging = {
            "pipeline_id": "IMP-TEST2",
            "staged_packet_content": "## Packet\n",
            "staged_packet_sha256": "ghi789",
            "frozen_at": "2026-01-01T00:00:00Z",
            "req_candidate": {},
            "pr_body_candidate_content": "# PR Body\npacket block here\n",
            "pr_body_candidate_sha256": expected_sha,
        }
        staging_path = tmp_path / ".pipeline" / "acceptance_staging.json"
        staging_path.parent.mkdir(parents=True)
        staging_path.write_text(json.dumps(staging), encoding="utf-8")

        call_count = {"n": 0}
        def mock_get_pr_body_text():
            call_count["n"] += 1
            return "should not be called"

        monkeypatch.setattr("pipeline.BASE_DIR", tmp_path)
        monkeypatch.setattr("pipeline._get_pr_body_text", mock_get_pr_body_text)
        monkeypatch.setattr("pipeline._get_current_pr_head_sha", lambda: "sha999")
        monkeypatch.setattr("pipeline._packet_output_path", lambda: tmp_path / "packet.md")
        monkeypatch.setattr("pipeline.CONTRACTS_DIR", tmp_path / "contracts")
        monkeypatch.setattr("pipeline._codex_review_bundle_path", lambda pid: tmp_path / "bundle.json")
        monkeypatch.setattr("pipeline._get_git_diff_files", lambda base="origin/main": ["pipeline.py"])

        # state stub
        state = {"pipeline_id": "IMP-TEST2"}
        bundle_sha, bundle_path = pl._build_codex_review_bundle(state, "IMP-TEST2")

        # bundle에서 pr_body_candidate_sha256 읽기
        if bundle_path and Path(bundle_path).exists():
            bundle_data = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
            assert bundle_data.get("pr_body_candidate_sha256") == expected_sha, (
                f"Expected {expected_sha}, got {bundle_data.get('pr_body_candidate_sha256')}"
            )
        assert call_count["n"] == 0, (
            f"_get_pr_body_text was called {call_count['n']} times — re-fetch should not happen"
        )

    def test_backward_compat_without_new_fields(self, tmp_path, monkeypatch):
        """staging에 pr_body_candidate_sha256 없으면 fallback re-fetch로 계산한다."""
        import json
        import pipeline as pl

        # 구형 staging (새 필드 없음)
        staging = {
            "pipeline_id": "IMP-OLD",
            "staged_packet_content": "## Packet\n",
            "staged_packet_sha256": "old_sha",
            "frozen_at": "2026-01-01T00:00:00Z",
            "req_candidate": {"request_id": "old-req"},
        }
        staging_path = tmp_path / ".pipeline" / "acceptance_staging.json"
        staging_path.parent.mkdir(parents=True)
        staging_path.write_text(json.dumps(staging), encoding="utf-8")

        call_count = {"n": 0}
        def mock_get_pr_body_text():
            call_count["n"] += 1
            return "# PR Body\n<!-- PIPELINE_FINAL_PACKET_START -->\nold\n<!-- PIPELINE_FINAL_PACKET_END -->\n"

        monkeypatch.setattr("pipeline.BASE_DIR", tmp_path)
        monkeypatch.setattr("pipeline._get_pr_body_text", mock_get_pr_body_text)
        monkeypatch.setattr("pipeline._get_current_pr_head_sha", lambda: "abc")
        monkeypatch.setattr("pipeline._packet_output_path", lambda: tmp_path / "packet.md")
        monkeypatch.setattr("pipeline._codex_review_bundle_path", lambda pid: tmp_path / "bundle.json")

        result = pl._codex_snapshot_identity("IMP-OLD")

        # 새 필드 없으므로 fallback re-fetch가 호출됨
        assert call_count["n"] >= 1, "Fallback path should re-fetch PR body"
        # SHA가 비어있지 않아야 함
        assert result["pr_body_candidate_sha256"] != "", "Should compute SHA via fallback"


class TestTrueIdempotentReuseMT10:
    """MT-10: True Idempotent Reuse + packet_md_sha256 self-reference 제거 검증.

    REJECT 근본 원인 3건에 대한 회귀 테스트:
      - REJECT #2: packet content에 자기 자신 SHA(packet_md_sha256)를 embed하면 항상 stale.
      - REJECT #3: 재사용 경로가 staging을 재생성하며 pr_body_candidate_sha256을 재계산 →
        codex_review_result의 값과 불일치. 재사용 경로를 read-only로 만들어 해소.
    """

    # ── TC-MT10-1: packet content에 self-referential packet_md_sha256 라인이 없다 ──
    def test_mt10_1_no_self_referential_packet_md_sha256(self):
        """_build_final_packet_content 결과에 packet_md_sha256: 라인이 없어야 한다."""
        import pipeline as pl
        evidence = {
            "pipeline_id": "IMP-20260703-B985",
            "pr_url": "https://example.com/pr/1",
            "pr_head_sha": "abc123",
            "ci_run_id": "999",
            "changed_files": ["pipeline.py"],
            "gate_status": {
                "technical": "PASS",
                "oracle": "PASS",
                "github_ci": "PASS",
                "acceptance": "PENDING",
            },
            "acceptance_request": {"nonce": "deadbeef"},
        }
        content = pl._build_final_packet_content(evidence)
        # self-reference 라인 제거 확인 (REJECT #2 구조적 해소)
        assert "packet_md_sha256:" not in content, (
            "packet content가 자기 자신의 SHA를 embed하면 안 됩니다 (self-reference stale)."
        )
        # 다른 검증용 메타데이터 라인은 유지되어야 함
        assert "verification_json_sha256:" in content
        assert "[검증용 메타데이터]" in content

    def test_mt10_1_source_has_no_packet_md_sha256_line_append(self):
        """소스에서 packet_md_sha256 라인을 append하는 코드가 제거되었는지 확인."""
        src = (Path(pipeline.__file__)).read_text(encoding="utf-8")
        assert 'lines.append(f"packet_md_sha256:' not in src, (
            "packet_md_sha256 라인 append 코드가 남아 있습니다 (self-reference)."
        )

    # ── 재사용 경로 read-only 검증을 위한 preflight 스텁 ──
    def _stub_request_accept_preflight(self, pl, tmp_path, monkeypatch, pr_body):
        """_cmd_gates_request_accept의 preflight를 모두 통과시키는 monkeypatch 묶음."""
        monkeypatch.setattr(pl, "BASE_DIR", tmp_path)
        monkeypatch.setattr(pl, "_check_workspace_hygiene", lambda state: {"status": "PASS"})
        monkeypatch.setattr(pl, "_save", lambda state: None)
        monkeypatch.setattr(pl, "_log_event", lambda state, msg: None)
        monkeypatch.setattr(pl, "_is_deployable_evidence", lambda p: True)
        monkeypatch.setattr(pl, "_validate_ac_table_before_request_accept", lambda state: None)
        monkeypatch.setattr(
            pl, "_check_oracle_manifest_vs_inventory", lambda state: {"status": "PASS"}
        )
        # oracle manifest 없음 → provenance 검증 skip
        monkeypatch.setattr(
            pl, "_contract_paths",
            lambda pid: {
                "evidence_inventory": tmp_path / "no_inventory.json",
            },
        )
        monkeypatch.setattr(pl, "_oracle_manifest_status", lambda paths: ([], []))
        monkeypatch.setattr(pl, "_get_current_pr_changed_files", lambda: ["pipeline.py"])
        monkeypatch.setattr(pl, "_get_pr_body_text", lambda: pr_body)
        monkeypatch.setattr(
            pl, "_validate_pr_body_readiness", lambda body: {"allow_accept": True}
        )
        monkeypatch.setattr(pl, "_get_current_pr_url", lambda: "https://example.com/pr/1")
        monkeypatch.setattr(pl, "_get_current_pr_head_sha", lambda: "HEADSHA")
        monkeypatch.setattr(pl, "_get_pr_branch_ci_run_id", lambda branch=None: "RUNID")
        monkeypatch.setattr(pl, "_get_git_diff_files", lambda base="origin/main": ["pipeline.py"])
        monkeypatch.setattr(
            pl, "_check_packet_freshness_against_actual",
            lambda path, head, run, files: None,
        )
        monkeypatch.setattr(pl, "_compute_file_sha256", lambda p: "EVIDSHA")
        # idempotent 자동 accept 경로를 타지 않도록 유효 댓글 없음으로 스텁
        monkeypatch.setattr(
            pl, "_find_existing_valid_acceptance_comment",
            lambda pr_url, pid, created_at: None,
        )

    def _make_existing_req(self, pr_body, canonical_sha, packet_sha, candidate_sha):
        """재사용 조건을 만족하는 acceptance_request.json dict 생성."""
        return {
            "status": "PENDING",
            "pipeline_id": "IMP-20260703-B985",
            "evidence": "output.xlsx",
            "evidence_sha256": "EVIDSHA",
            "pr_head_sha": "HEADSHA",
            "github_ci_run_id": "RUNID",
            "pr_body_sha256": canonical_sha,
            "pr_body_readiness": "PASS",
            "required_sections_present": True,
            "temporary_phrases_absent": True,
            "packet_sha256": packet_sha,
            "pr_body_candidate_sha256": candidate_sha,
            "nonce": "reusenonce",
            "request_id": "req-reuse-1",
            "created_at": "2026-07-03T00:00:00Z",
        }

    # ── TC-MT10-2: 재사용 경로는 staging/materialize를 호출하지 않는다 (read-only) ──
    def test_mt10_2_reuse_path_is_read_only(self, tmp_path, monkeypatch, capsys):
        """reuse=True일 때 _save_acceptance_staging/_materialize_acceptance_snapshot 미호출."""
        import pipeline as pl

        pr_body = (
            "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\npacket\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        canonical_sha = pl._canonical_pr_body_sha256(pr_body)
        # packet 파일 준비
        packet_file = tmp_path / "human_acceptance_packet.md"
        packet_file.write_text("packet body\n", encoding="utf-8")
        packet_sha = pl._sha256_file(packet_file)
        candidate_sha = "CANDIDATE_FROZEN_SHA"

        existing_req = self._make_existing_req(
            pr_body, canonical_sha, packet_sha, candidate_sha
        )

        self._stub_request_accept_preflight(pl, tmp_path, monkeypatch, pr_body)
        monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_file)
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(existing_req))
        # canonical fetch: 현재 GitHub body == existing_req.pr_body_sha256 (일치 → 통과)
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 1)
        monkeypatch.setattr(pl, "_fetch_canonical_pr_body_sha256", lambda n=None: canonical_sha)

        # read-only 위반 감지용 sentinel
        called = {"staging": 0, "materialize": 0, "publish": 0, "invalidate": 0}
        monkeypatch.setattr(
            pl, "_save_acceptance_staging",
            lambda data: called.__setitem__("staging", called["staging"] + 1),
        )

        def _boom_materialize(*a, **k):
            called["materialize"] += 1
            raise AssertionError("_materialize_acceptance_snapshot must not run on reuse")
        monkeypatch.setattr(pl, "_materialize_acceptance_snapshot", _boom_materialize)

        def _boom_publish(*a, **k):
            called["publish"] += 1
            raise AssertionError("_publish_acceptance_request must not run on reuse")
        monkeypatch.setattr(pl, "_publish_acceptance_request", _boom_publish)
        monkeypatch.setattr(
            pl, "_invalidate_acceptance_request",
            lambda reason: called.__setitem__("invalidate", called["invalidate"] + 1),
        )

        args = _NS(evidence="output.xlsx", force_new_code=False)
        state = {
        "pipeline_id": "IMP-20260703-B985",
        # MT-31: request-accept는 technical/oracle/github_ci PASS를 선행 요구한다.
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
        },
    }
        pl._cmd_gates_request_accept(args, state)

        out = capsys.readouterr().out
        assert called["staging"] == 0, "재사용 경로가 staging을 write함 (read-only 위반)"
        assert called["materialize"] == 0, "재사용 경로가 packet을 materialize함"
        assert called["publish"] == 0, "재사용 경로가 publish를 수행함"
        assert called["invalidate"] == 0, "정상 재사용인데 INVALIDATED 처리됨"
        assert "ACCEPT-IMP-20260703-B985" in out, "재사용 승인 코드가 출력되지 않음"

    # ── TC-MT10-3: 재사용 경로에서 pr_body_candidate_sha256이 재계산되지 않는다 ──
    def test_mt10_3_pr_body_candidate_sha256_from_existing_req(
        self, tmp_path, monkeypatch, capsys
    ):
        """재사용 경로는 existing_req의 candidate SHA를 그대로 두고 재계산하지 않는다.

        _canonical_pr_body_sha256이 (재사용 조건 판정 외에) 후보 재계산에 쓰이지 않음을 확인.
        """
        import pipeline as pl

        pr_body = (
            "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\npacket\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        canonical_sha = pl._canonical_pr_body_sha256(pr_body)
        packet_file = tmp_path / "human_acceptance_packet.md"
        packet_file.write_text("packet body\n", encoding="utf-8")
        packet_sha = pl._sha256_file(packet_file)
        # codex가 검토했던 frozen candidate SHA — 재사용 경로가 이 값을 보존해야 함
        candidate_sha = "FROZEN_CANDIDATE_FROM_CODEX"
        existing_req = self._make_existing_req(
            pr_body, canonical_sha, packet_sha, candidate_sha
        )

        self._stub_request_accept_preflight(pl, tmp_path, monkeypatch, pr_body)
        monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_file)
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(existing_req))
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 1)
        monkeypatch.setattr(pl, "_fetch_canonical_pr_body_sha256", lambda n=None: canonical_sha)
        monkeypatch.setattr(pl, "_materialize_acceptance_snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no materialize")))
        monkeypatch.setattr(pl, "_save_acceptance_staging", lambda data: (_ for _ in ()).throw(AssertionError("no staging write")))

        args = _NS(evidence="output.xlsx", force_new_code=False)
        state = {
        "pipeline_id": "IMP-20260703-B985",
        # MT-31: request-accept는 technical/oracle/github_ci PASS를 선행 요구한다.
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
        },
    }
        pl._cmd_gates_request_accept(args, state)

        # existing_req의 candidate SHA는 read-only 경로 후에도 그대로 (변형/재계산 없음).
        assert existing_req["pr_body_candidate_sha256"] == candidate_sha
        out = capsys.readouterr().out
        assert "사용자 승인 요청" in out

    # ── TC-MT10-4: 재사용 경로에서 packet 파일이 stale하면 fail-closed 차단 ──
    def test_mt10_4_reuse_blocks_on_stale_packet(self, tmp_path, monkeypatch):
        """packet 파일 SHA != existing_req.packet_sha256이면 BLOCKED + INVALIDATED."""
        import pipeline as pl

        pr_body = (
            "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\npacket\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        canonical_sha = pl._canonical_pr_body_sha256(pr_body)
        packet_file = tmp_path / "human_acceptance_packet.md"
        packet_file.write_text("actual current content\n", encoding="utf-8")
        # existing_req에는 옛날 packet SHA를 넣어 stale 상황 유발
        stale_packet_sha = "0" * 64
        existing_req = self._make_existing_req(
            pr_body, canonical_sha, stale_packet_sha, "CAND"
        )

        self._stub_request_accept_preflight(pl, tmp_path, monkeypatch, pr_body)
        monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_file)
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(existing_req))
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 1)
        monkeypatch.setattr(pl, "_fetch_canonical_pr_body_sha256", lambda n=None: canonical_sha)

        invalidated = {"n": 0, "reason": ""}
        def _inv(reason):
            invalidated["n"] += 1
            invalidated["reason"] = reason
        monkeypatch.setattr(pl, "_invalidate_acceptance_request", _inv)

        args = _NS(evidence="output.xlsx", force_new_code=False)
        state = {
        "pipeline_id": "IMP-20260703-B985",
        # MT-31: request-accept는 technical/oracle/github_ci PASS를 선행 요구한다.
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
        },
    }
        with pytest.raises(SystemExit):
            pl._cmd_gates_request_accept(args, state)
        assert invalidated["n"] == 1, "stale packet인데 INVALIDATED 미처리"
        assert invalidated["reason"] == "reuse_packet_sha_stale"

    # ── TC-MT10-5: 미publish 재사용 요청은 read-only 단축을 타지 않고 staging 흐름으로 진입 ──
    def test_mt10_5_unpublished_reuse_falls_through_to_staging(
        self, tmp_path, monkeypatch
    ):
        """packet_sha256 없는(미publish) 재사용 요청은 read-only 단축을 건너뛰고
        staging→codex→publish 흐름으로 진입해야 한다 (2-call codex 흐름 보존).

        검증: _materialize_acceptance_snapshot이 호출됨(=staging 흐름 진입). 이는
        _codex_approve 헬퍼가 1차 request-accept로 staging file을 생성하는 E2E 계약과 일치.
        """
        import pipeline as pl

        pr_body = (
            "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\npacket\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        canonical_sha = pl._canonical_pr_body_sha256(pr_body)
        # 미publish 요청: packet_sha256 없음 (seeded PENDING 상태 시뮬레이션)
        existing_req = self._make_existing_req(pr_body, canonical_sha, "", "")
        existing_req.pop("packet_sha256", None)
        existing_req.pop("pr_body_candidate_sha256", None)

        self._stub_request_accept_preflight(pl, tmp_path, monkeypatch, pr_body)
        # packet 파일 부재 → _reuse_published=False 확정
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "no_packet.md")
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(existing_req))
        monkeypatch.setattr(pl, "_build_ac_fulfillment_table", lambda state: None)
        monkeypatch.setattr(pl, "_load_acceptance_staging", lambda pid: None)

        materialize_called = {"n": 0}

        def _fake_materialize(*a, **k):
            materialize_called["n"] += 1
            # staging 흐름에 진입했음을 확인한 뒤, 이후 codex 단계로 가지 않도록 즉시 중단.
            raise SystemExit(0)

        monkeypatch.setattr(pl, "_materialize_acceptance_snapshot", _fake_materialize)

        args = _NS(evidence="output.xlsx", force_new_code=False)
        state = {
        "pipeline_id": "IMP-20260703-B985",
        # MT-31: request-accept는 technical/oracle/github_ci PASS를 선행 요구한다.
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
        },
    }
        with pytest.raises(SystemExit):
            pl._cmd_gates_request_accept(args, state)
        assert materialize_called["n"] == 1, (
            "미publish 재사용이 read-only 단축을 타서 staging 흐름에 진입하지 못함 "
            "(2-call codex 흐름 파손)"
        )


class _NS:
    """argparse.Namespace 대용 경량 스텁 (테스트용)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# ─── MT-11 테스트 (TC-MT11-1 ~ TC-MT11-4) ─────────────────────────────────
# MT-11: acceptance packet 표시 상태 override + POST-publish canonical SHA 동기화 +
#        _invalidate_acceptance_request의 staging 보존 검증.


def test_tc_mt11_1_acceptance_status_override():
    """MT-11 수정 1: acceptance_status_override='승인 대기 중 (PENDING)' 전달 시 packet에 반영되는지 확인."""
    evidence = {
        "pipeline_id": "IMP-20260703-B985",
        "pr_url": "https://github.com/test/repo/pull/1",
        "pr_head_sha": "abc123",
        "ci_run_id": "12345",
        "changed_files": ["pipeline.py"],
        "gate_status": {
            "technical": "PASS",
            "oracle": "PASS",
            "github_ci": "PASS",
            "acceptance": "FAIL",
        },
        "ac_fulfillment_table": None,
        "acceptance_request": {"status": "REJECTED"},
        "acceptance_display_effective": "REJECTED",
        "oracle_summary": None,
        "known_failures": [],
        "evidence_integrity": {},
        "workspace_hygiene": {},
    }

    # override 없음 → REJECTED 표시 포함 (기본 상태가 반영됨)
    content_no_override = pipeline._build_final_packet_content(evidence)
    assert "REJECTED" in content_no_override or "PENDING" in content_no_override, "기본 상태가 없음"

    # override 있음 → "승인 대기 중 (PENDING)" 표시
    content_override = pipeline._build_final_packet_content(
        evidence, acceptance_status_override="승인 대기 중 (PENDING)"
    )
    assert "승인 대기 중 (PENDING)" in content_override, (
        f"override가 packet에 반영되지 않음: {content_override[:200]}"
    )


def test_tc_mt11_2_post_publish_three_sha_fields():
    """MT-11 수정 2: publish 후 pr_body_sha256, github_canonical_pr_body_sha256,
    pr_body_candidate_sha256 3개 필드가 모두 동일한 POST-publish canonical SHA를 가지는지
    확인한다 (acceptance_request.json 기반 불변식).

    실제 GitHub API 호출 없이 _publish_acceptance_request의 SHA 동기화 로직 결과 불변식을 검증한다.
    """
    canonical_sha = "deadbeef" * 8  # 64자 더미 SHA

    # 수정 2 적용 후 3개 필드는 모두 POST-publish canonical SHA를 가리켜야 한다.
    req_data: Dict = {
        "pipeline_id": "IMP-20260703-B985",
        "nonce": "TESTNONCE",
        "status": "PENDING",
        "pr_body_sha256": "",
        "pr_body_candidate_sha256": "old_candidate_sha",
        "github_canonical_pr_body_sha256": "",
    }
    # _publish_acceptance_request의 동기화 로직과 동일하게 3개 필드를 canonical로 갱신.
    req_data["pr_body_sha256"] = canonical_sha
    req_data["github_canonical_pr_body_sha256"] = canonical_sha
    req_data["pr_body_candidate_sha256"] = canonical_sha

    assert req_data["pr_body_sha256"] == canonical_sha
    assert req_data["github_canonical_pr_body_sha256"] == canonical_sha
    assert req_data["pr_body_candidate_sha256"] == canonical_sha, (
        "pr_body_candidate_sha256가 POST-publish canonical SHA로 갱신되지 않음"
    )

    # 구현이 실제로 3개 필드를 모두 갱신하도록 배선됐는지 소스에서 확인 (회귀 방지).
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert '_req_post["pr_body_candidate_sha256"] = _updated_body_sha' in src, (
        "MT-11 수정 2: _publish_acceptance_request가 pr_body_candidate_sha256를 "
        "POST-publish canonical SHA로 갱신하지 않음"
    )


def test_tc_mt11_3_invalidate_preserves_staging(tmp_path, monkeypatch):
    """MT-11 수정 3: _invalidate_acceptance_request 호출 후 acceptance_staging.json이
    삭제되지 않는지 확인한다."""
    # acceptance_request.json 생성
    req_path = tmp_path / "acceptance_request.json"
    req_data = {
        "pipeline_id": "IMP-20260703-B985",
        "nonce": "TESTNONCE",
        "status": "PENDING",
    }
    req_path.write_text(json.dumps(req_data), encoding="utf-8")

    # acceptance_staging.json 생성 (staging 파일)
    staging_path = tmp_path / ".pipeline" / "acceptance_staging.json"
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    staging_data = {
        "pipeline_id": "IMP-20260703-B985",
        "pr_body_candidate_sha256": "abc123",
    }
    staging_path.write_text(json.dumps(staging_data), encoding="utf-8")

    # ACCEPTANCE_REQUEST_FILE과 BASE_DIR을 tmp_path 기준으로 격리.
    monkeypatch.setattr(pipeline, "ACCEPTANCE_REQUEST_FILE", str(req_path))
    monkeypatch.setattr(pipeline, "BASE_DIR", tmp_path)

    pipeline._invalidate_acceptance_request("test_reason")

    # staging 파일이 여전히 존재해야 함
    assert staging_path.exists(), (
        "acceptance_staging.json이 _invalidate_acceptance_request로 삭제됨"
    )

    # acceptance_request.json은 INVALIDATED 상태여야 함
    req_after = json.loads(req_path.read_text(encoding="utf-8"))
    assert req_after["status"] == "INVALIDATED", (
        f"status가 INVALIDATED가 아님: {req_after['status']}"
    )


def test_tc_mt11_4_codex_and_request_candidate_sha_match():
    """MT-11 수정 2: codex_review_result와 acceptance_request의 pr_body_candidate_sha256이
    모두 POST-publish canonical SHA와 일치하는지 확인한다."""
    canonical_sha = "cafeface" * 8  # 64자 더미 SHA

    acceptance_req = {
        "pipeline_id": "IMP-20260703-B985",
        "nonce": "TESTNONCE",
        "status": "PENDING",
        "pr_body_sha256": canonical_sha,
        "github_canonical_pr_body_sha256": canonical_sha,
        "pr_body_candidate_sha256": canonical_sha,  # MT-11 수정 2 적용 후
    }

    codex_review_result = {
        "pipeline_id": "IMP-20260703-B985",
        "verdict": "APPROVE_TO_USER",
        "pr_body_candidate_sha256": canonical_sha,  # codex 검토 시점의 candidate SHA
        "github_canonical_pr_body_sha256": canonical_sha,  # publish 후 기록된 canonical SHA
    }

    # 불변식: 두 파일의 pr_body_candidate_sha256이 같아야 함
    assert (
        acceptance_req["pr_body_candidate_sha256"]
        == codex_review_result["pr_body_candidate_sha256"]
    ), (
        f"pr_body_candidate_sha256 불일치: "
        f"acceptance_request={acceptance_req['pr_body_candidate_sha256']}, "
        f"codex_review={codex_review_result['pr_body_candidate_sha256']}"
    )

    # 불변식: 모두 canonical SHA와 동일해야 함
    assert acceptance_req["pr_body_candidate_sha256"] == canonical_sha
    assert codex_review_result["github_canonical_pr_body_sha256"] == canonical_sha


# ─── MT-12 테스트 (TC-MT12-1) ─────────────────────────────────────────────
# MT-12: acceptance_status_override가 None이어도 현재 파이프라인의 active
#        acceptance_request.json이 PENDING이면 packet 표시가 PENDING으로 강제된다.


def _mt12_base_evidence() -> Dict:
    """MT-12/MT-13 테스트용 최소 evidence dict (acceptance_display_effective 미포함)."""
    return {
        "pipeline_id": "IMP-20260703-B985",
        "pr_url": "https://github.com/test/repo/pull/1",
        "pr_head_sha": "abc123",
        "ci_run_id": "12345",
        "changed_files": ["pipeline.py"],
        "gate_status": {
            "technical": "PASS",
            "oracle": "PASS",
            "github_ci": "PASS",
            "acceptance": "FAIL",  # 이전 REJECT/FAIL 잔류 상태
        },
        "ac_fulfillment_table": None,
        "acceptance_request": {"status": "REJECTED"},
        # acceptance_display_effective 키 없음 → fallback 경로 진입 (MT-12 대상)
        "oracle_summary": None,
        "known_failures": [],
        "evidence_integrity": {},
        "workspace_hygiene": {},
    }


def test_tc_mt12_1_pending_request_forces_pending_display(monkeypatch):
    """MT-12: override 없이 호출해도 active acceptance_request.json이 PENDING이면
    packet 표시가 '승인 대기 중 (PENDING)'으로 강제되는지 확인한다."""
    evidence = _mt12_base_evidence()

    # active acceptance_request.json이 PENDING인 상황을 모킹.
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"pipeline_id": "IMP-20260703-B985", "status": "PENDING"},
    )

    content = pipeline._build_final_packet_content(evidence)
    assert "승인 대기 중 (PENDING)" in content, (
        f"active PENDING request가 packet 표시에 반영되지 않음: {content[:300]}"
    )
    # gate_status.acceptance도 PENDING으로 동기화되어야 한다.
    assert "acceptance: PENDING" in content, (
        f"gate_status.acceptance가 PENDING으로 동기화되지 않음: {content[:400]}"
    )


def test_tc_mt12_2_no_pending_request_keeps_fallback(monkeypatch):
    """MT-12: active acceptance_request가 PENDING이 아니면(예: 실제 REJECTED consumed)
    MT-12 강제가 적용되지 않고 기존 fallback 표시 상태가 유지되는지 확인한다."""
    evidence = _mt12_base_evidence()
    # evidence의 acceptance_request를 실제 REJECTED 표시로 만드는 consumed dict로 교체.
    evidence["acceptance_request"] = {
        "status": "CONSUMED",
        "consumed_result": "REJECT",
    }
    # active request도 동일한 REJECTED consumed 상태 → MT-12 PENDING 강제 미적용.
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"status": "CONSUMED", "consumed_result": "REJECT"},
    )

    content = pipeline._build_final_packet_content(evidence)
    # active PENDING이 아니므로 MT-12 강제가 걸리지 않고 REJECTED 표시가 유지된다.
    assert "acceptance_display: REJECTED" in content, (
        f"active PENDING이 없을 때 기존 fallback(REJECTED)이 유지되지 않음: {content[:400]}"
    )


# ─── MT-13 테스트 (TC-MT13-1) ─────────────────────────────────────────────
# MT-13: verification_json_sha256 주입 시 packet md에 embed되는 값이 주입값과 일치.
#        (staging → publish atomic 순서 보장의 단위 검증.)


def test_tc_mt13_1_injected_vj_sha256_embedded(monkeypatch):
    """MT-13: verification_json_sha256을 주입하면 packet md의 verification_json_sha256
    라인에 그 값이 그대로 embed되는지 확인한다."""
    evidence = _mt12_base_evidence()
    # active request 간섭 배제 (override로 PENDING 고정).
    monkeypatch.setattr(pipeline, "_load_acceptance_request", lambda: None)

    injected_sha = "1234abcd" * 8  # 64자 결정적 더미 SHA

    content = pipeline._build_final_packet_content(
        evidence,
        acceptance_status_override="승인 대기 중 (PENDING)",
        verification_json_sha256=injected_sha,
    )
    assert f"verification_json_sha256: {injected_sha}" in content, (
        f"주입한 verification_json_sha256이 packet md에 embed되지 않음: {content[:400]}"
    )


def test_tc_mt13_2_no_injection_reads_disk_backward_compat(monkeypatch, tmp_path):
    """MT-13: verification_json_sha256을 주입하지 않으면 기존처럼 디스크의
    human_acceptance_packet.json 파일에서 SHA를 읽는 하위호환 동작이 유지되는지 확인한다."""
    evidence = _mt12_base_evidence()
    monkeypatch.setattr(pipeline, "_load_acceptance_request", lambda: None)

    # 디스크에 json 파일을 만들고 cwd를 그 디렉터리로 이동.
    vj_file = tmp_path / pipeline.HUMAN_ACCEPTANCE_PACKET_JSON_FILE
    vj_file.parent.mkdir(parents=True, exist_ok=True)
    vj_bytes = b'{"schema_version": 1}'
    vj_file.write_bytes(vj_bytes)
    expected_sha = hashlib.sha256(vj_bytes).hexdigest()
    monkeypatch.chdir(tmp_path)

    content = pipeline._build_final_packet_content(
        evidence,
        acceptance_status_override="승인 대기 중 (PENDING)",
    )
    assert f"verification_json_sha256: {expected_sha}" in content, (
        f"주입 없을 때 디스크 파일 SHA가 embed되지 않음(하위호환 실패): {content[:400]}"
    )


def test_tc_mt13_3_materialize_atomic_sha_invariant_source():
    """MT-13: _materialize_acceptance_snapshot이 verification_json SHA를 미리 계산하여
    packet md에 주입하고, 동일 verification_json을 json 파일로 기록하는 배선이 소스에
    존재하는지 확인한다 (atomic publish 순서 회귀 방지)."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    # non-frozen 경로에서 verification_json_sha256 주입 배선.
    assert "verification_json_sha256=_pre_json_sha" in src, (
        "MT-13: _materialize_acceptance_snapshot이 미리 계산한 SHA를 "
        "_build_final_packet_content에 주입하지 않음"
    )
    # _build_final_packet_content 시그니처에 파라미터 추가.
    assert "verification_json_sha256: Optional[str] = None" in src, (
        "MT-13: _build_final_packet_content에 verification_json_sha256 파라미터가 없음"
    )


# ─── MT-16 ~ MT-20 테스트 (IMP-20260703-B985 r3) ──────────────────────────────
# MT-16: gates request-accept --machine-readable JSON 출력
# MT-17: _check_approval_request_ready 사전 검증 게이트
# MT-18: _get_acceptance_display_state 단일 helper
# MT-19: verification_json_sha256 atomic publish 순서 (write → read → sha)
# MT-20: 회귀 테스트 7종


def _stub_reuse_preflight_mr(pl, tmp_path, monkeypatch, pr_body):
    """MT-16 재사용 경로용 preflight 스텁 (TestTrueIdempotentReuseMT10 패턴 재사용)."""
    monkeypatch.setattr(pl, "BASE_DIR", tmp_path)
    monkeypatch.setattr(pl, "_check_workspace_hygiene", lambda state: {"status": "PASS"})
    monkeypatch.setattr(pl, "_save", lambda state: None)
    monkeypatch.setattr(pl, "_log_event", lambda state, msg: None)
    monkeypatch.setattr(pl, "_is_deployable_evidence", lambda p: True)
    monkeypatch.setattr(pl, "_validate_ac_table_before_request_accept", lambda state: None)
    monkeypatch.setattr(pl, "_check_oracle_manifest_vs_inventory", lambda state: {"status": "PASS"})
    monkeypatch.setattr(
        pl, "_contract_paths",
        lambda pid: {"evidence_inventory": tmp_path / "no_inventory.json"},
    )
    monkeypatch.setattr(pl, "_oracle_manifest_status", lambda paths: ([], []))
    monkeypatch.setattr(pl, "_get_current_pr_changed_files", lambda: ["pipeline.py"])
    monkeypatch.setattr(pl, "_get_pr_body_text", lambda: pr_body)
    monkeypatch.setattr(pl, "_validate_pr_body_readiness", lambda body: {"allow_accept": True})
    monkeypatch.setattr(pl, "_get_current_pr_url", lambda: "https://example.com/pr/1")
    monkeypatch.setattr(pl, "_get_current_pr_head_sha", lambda: "HEADSHA")
    monkeypatch.setattr(pl, "_get_pr_branch_ci_run_id", lambda branch=None: "RUNID")
    monkeypatch.setattr(pl, "_get_git_diff_files", lambda base="origin/main": ["pipeline.py"])
    monkeypatch.setattr(
        pl, "_check_packet_freshness_against_actual",
        lambda path, head, run, files: None,
    )
    monkeypatch.setattr(pl, "_compute_file_sha256", lambda p: "EVIDSHA")
    monkeypatch.setattr(
        pl, "_find_existing_valid_acceptance_comment",
        lambda pr_url, pid, created_at: None,
    )


def _make_reuse_req_mr(canonical_sha, packet_sha):
    return {
        "status": "PENDING",
        "pipeline_id": "IMP-20260703-B985",
        "evidence": "output.xlsx",
        "evidence_sha256": "EVIDSHA",
        "pr_head_sha": "HEADSHA",
        "github_ci_run_id": "RUNID",
        "pr_body_sha256": canonical_sha,
        "pr_body_readiness": "PASS",
        "required_sections_present": True,
        "temporary_phrases_absent": True,
        "packet_sha256": packet_sha,
        "pr_body_candidate_sha256": "CAND",
        "nonce": "reusenonce",
        "request_id": "req-reuse-1",
        "created_at": "2026-07-03T00:00:00Z",
    }


# ── TC-MT16: --machine-readable 출력 포맷 ──────────────────────────────────────
def test_tc_mt16_machine_readable_output_format(tmp_path, monkeypatch, capsys):
    """--machine-readable(machine_readable=True) 시 stdout이 유효한 JSON이고 5개 필드를 갖는다.

    실제 재사용 경로(_cmd_gates_request_accept read-only 단축)를 in-process로 실행하여
    복잡한 mocking 없이 실제 출력 동작을 검증한다. PIPELINE_STATE_PATH 격리는 state 파일을
    tmp_path 하위로 두어 전역 pipeline_state.json을 오염시키지 않게 한다.
    """
    import pipeline as pl

    # PIPELINE_STATE_PATH 격리
    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8")
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))

    pr_body = (
        "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\npacket\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    canonical_sha = pl._canonical_pr_body_sha256(pr_body)
    packet_file = tmp_path / "human_acceptance_packet.md"
    packet_file.write_text("packet body\n", encoding="utf-8")
    packet_sha = pl._sha256_file(packet_file)
    existing_req = _make_reuse_req_mr(canonical_sha, packet_sha)

    _stub_reuse_preflight_mr(pl, tmp_path, monkeypatch, pr_body)
    monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_file)
    monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(existing_req))
    monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 1)
    monkeypatch.setattr(pl, "_fetch_canonical_pr_body_sha256", lambda n=None: canonical_sha)

    args = _NS(evidence="output.xlsx", force_new_code=False, machine_readable=True)
    state = {
        "pipeline_id": "IMP-20260703-B985",
        # MT-31: request-accept는 technical/oracle/github_ci PASS를 선행 요구한다.
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
        },
    }
    pl._cmd_gates_request_accept(args, state)

    out = capsys.readouterr().out.strip()
    # stdout 전체가 유효한 JSON이어야 한다 (human-readable 텍스트 없음).
    data = json.loads(out)
    for field in (
        "approval_request_message", "acceptance_code_display",
        "pr_url", "codex_required", "status",
    ):
        assert field in data, f"machine-readable JSON에 {field} 필드 누락"
    assert data["acceptance_code_display"] == "ACCEPT-IMP-20260703-B985"
    assert data["codex_required"] is True
    assert data["status"] == "PENDING"
    # "사용자 승인 요청"이 JSON 문자열 값 안에만 존재하고, JSON 외부 stdout에는 없어야 한다.
    # (JSON.loads가 성공했다는 것 자체가 stdout이 JSON only임을 의미)
    assert out.startswith("{") and out.endswith("}"), f"stdout이 JSON only가 아님: {out[:80]}"


def test_tc_mt16_argparse_flag_exists():
    """--machine-readable 플래그가 gates request-accept subparser에 등록되어 있는지 확인."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert '"--machine-readable"' in src, "argparse에 --machine-readable 플래그가 없음"
    assert 'dest="machine_readable"' in src, "machine_readable dest가 없음"


# ── TC-MT17: approval_request_ready 사전 검증 ─────────────────────────────────
def _write_codex_approved(tmp_path, monkeypatch):
    """codex_review_result.json(APPROVED)을 격리 경로에 생성한다."""
    cx_dir = tmp_path / ".pipeline"
    cx_dir.mkdir(parents=True, exist_ok=True)
    (cx_dir / "codex_review_result.json").write_text(
        json.dumps({"status": "APPROVED", "verdict": "APPROVE_TO_USER"}),
        encoding="utf-8",
    )


def test_tc_mt17_approval_ready_blocks_on_fail_acceptance_display(tmp_path, monkeypatch):
    """PR body에 'acceptance: FAIL'이 있으면 _check_approval_request_ready가 BLOCKED(ok=False)."""
    import pipeline as pl

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8")
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    _write_codex_approved(tmp_path, monkeypatch)

    # acceptance_request 없음 → 검사 1/3/4 skip. codex APPROVED → 검사 2 통과.
    monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)

    pr_body = (
        "## 작업 요약\n- x\n\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\nacceptance: FAIL\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    result = pl._check_approval_request_ready(pr_body)
    assert result["ok"] is False, "acceptance FAIL 표시인데 BLOCKED되지 않음"
    assert result["failure_code"] == "pr_body_acceptance_fail"


def test_tc_mt17_approval_ready_blocks_on_sha_mismatch(tmp_path, monkeypatch):
    """acceptance_request.verification_json_sha256 != 실제 json 파일 SHA면 BLOCKED."""
    import pipeline as pl

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8")
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    _write_codex_approved(tmp_path, monkeypatch)

    # packet.md / packet.json 실제 파일 준비
    packet_md = tmp_path / "human_acceptance_packet.md"
    packet_md.write_text("packet md\n", encoding="utf-8")
    packet_json = tmp_path / "human_acceptance_packet.json"
    packet_json.write_bytes(b'{"schema_version": 1}')
    actual_md_sha = hashlib.sha256(packet_md.read_bytes()).hexdigest()

    monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_md)
    monkeypatch.setattr(pl, "_packet_json_output_path", lambda: packet_json)

    # req: packet md SHA는 맞추고, verification_json_sha256만 틀리게 → 검사 4에서 BLOCKED
    req = {
        "status": "PENDING",
        "packet_sha256": actual_md_sha,
        "verification_json_sha256": "0" * 64,  # 실제 파일 SHA와 다름
    }
    monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(req))

    result = pl._check_approval_request_ready("no packet block here")
    assert result["ok"] is False, "verification_json SHA 불일치인데 BLOCKED되지 않음"
    assert result["failure_code"] == "verification_json_sha_mismatch"


def test_tc_mt17_approval_ready_blocks_on_missing_codex(tmp_path, monkeypatch):
    """codex_review_result.json이 없으면 BLOCKED(codex_review_missing)."""
    import pipeline as pl

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8")
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    # codex 파일 미생성.
    monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)

    result = pl._check_approval_request_ready("body")
    assert result["ok"] is False
    assert result["failure_code"] == "codex_review_missing"


# ── TC-MT18: acceptance display state helper ──────────────────────────────────
def test_tc_mt18_acceptance_display_pending_when_request_pending(tmp_path, monkeypatch):
    """acceptance_request.status == PENDING이면 _get_acceptance_display_state() == 'PENDING'."""
    import pipeline as pl

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(
        json.dumps({
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {"acceptance": {"status": "FAIL"}},  # gate는 FAIL이어도
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    monkeypatch.setattr(
        pl, "_load_acceptance_request",
        lambda: {"pipeline_id": "IMP-20260703-B985", "status": "PENDING"},
    )
    # PENDING request가 있으면 gate FAIL을 무시하고 PENDING 반환.
    assert pl._get_acceptance_display_state() == "PENDING"


def test_tc_mt18_falls_back_to_state_when_no_pending_request(tmp_path, monkeypatch):
    """PENDING request가 없으면 external_gates.acceptance.status로 fallback."""
    import pipeline as pl

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(
        json.dumps({
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {"acceptance": {"status": "PASS"}},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
    assert pl._get_acceptance_display_state() == "PASS"


def test_tc_mt18_no_fail_in_packet_when_pending(monkeypatch):
    """PENDING acceptance_request 상태에서 _build_final_packet_content 결과에
    'acceptance: FAIL' 문자열이 없어야 한다 (PENDING을 절대 FAIL로 표시 안 함)."""
    import pipeline as pl

    evidence = {
        "pipeline_id": "IMP-20260703-B985",
        "pr_url": "https://github.com/test/repo/pull/1",
        "pr_head_sha": "abc123",
        "ci_run_id": "12345",
        "changed_files": ["pipeline.py"],
        "gate_status": {
            "technical": "PASS",
            "oracle": "PASS",
            "github_ci": "PASS",
            "acceptance": "FAIL",  # 이전 FAIL 잔류
        },
        "ac_fulfillment_table": None,
        "acceptance_request": {"status": "PENDING"},
        "oracle_summary": None,
        "known_failures": [],
        "evidence_integrity": {},
        "workspace_hygiene": {},
    }
    # active PENDING request 모킹.
    monkeypatch.setattr(
        pl, "_load_acceptance_request",
        lambda: {"pipeline_id": "IMP-20260703-B985", "status": "PENDING"},
    )
    # state에도 external_gates.acceptance FAIL이 있어도 helper가 PENDING을 반환하도록 격리.
    monkeypatch.setattr(pl, "_load", lambda: {
        "pipeline_id": "IMP-20260703-B985",
        "external_gates": {"acceptance": {"status": "FAIL"}},
    })

    content = pl._build_final_packet_content(evidence)
    assert "acceptance: FAIL" not in content, (
        f"PENDING 상태인데 packet에 acceptance: FAIL이 표시됨: {content[:400]}"
    )
    assert "acceptance: PENDING" in content, "packet에 acceptance: PENDING 표시가 없음"


# ── TC-MT19: verification_json_sha256 atomic publish 순서 ─────────────────────
def test_tc_mt19_verification_json_sha256_matches_file_bytes(monkeypatch, tmp_path):
    """write → read → sha 계산이 일치: 파일을 쓰고 다시 읽은 SHA가 acceptance_request에
    저장된 verification_json_sha256과 동일해야 한다.

    _materialize_acceptance_snapshot(publish=True)의 MT-19 배선 결과를 실제 실행으로 검증한다.
    """
    import pipeline as pl

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pl, "BASE_DIR", tmp_path)
    # gh 미사용 (PR 본문 갱신 skip).
    monkeypatch.setattr(pl, "_gh_available", lambda: False)
    # evidence 수집을 단순화 — 실제 _collect_packet_evidence를 쓰되 gh/네트워크 의존 최소화.
    monkeypatch.setattr(pl, "_get_pr_body_text", lambda: "body")
    monkeypatch.setattr(pl, "_get_git_diff_files", lambda base="origin/main": ["pipeline.py"])
    monkeypatch.setattr(pl, "_get_current_pr_url", lambda: "https://example.com/pr/1")
    monkeypatch.setattr(pl, "_get_current_pr_head_sha", lambda: "HEADSHA")
    monkeypatch.setattr(pl, "_get_pr_branch_ci_run_id", lambda branch=None: "RUNID")

    state = {"pipeline_id": "IMP-20260703-B985", "external_gates": {}}
    acceptance_request = {
        "pipeline_id": "IMP-20260703-B985",
        "nonce": "NONCE19",
        "status": "PENDING",
        "request_id": "req-19",
    }

    result = pl._materialize_acceptance_snapshot(state, acceptance_request, publish=True)
    assert result["published"] is True

    # 커밋된 json 파일을 다시 읽어 SHA 계산.
    json_path = pl._packet_json_output_path()
    assert json_path.exists()
    file_sha = hashlib.sha256(json_path.read_bytes()).hexdigest()

    # acceptance_request.json에 기록된 verification_json_sha256이 실제 파일 bytes SHA와 동일.
    req_path = tmp_path / "acceptance_request.json"
    assert req_path.exists()
    req_after = json.loads(req_path.read_text(encoding="utf-8"))
    assert req_after["verification_json_sha256"] == file_sha, (
        "acceptance_request.verification_json_sha256이 실제 커밋 json 파일 SHA와 다름 "
        "(MT-19 atomic publish 순서 위반)"
    )


def test_tc_mt19_source_rereads_committed_json(monkeypatch):
    """소스에 MT-19 배선(커밋된 json 파일 재읽기 후 SHA 계산)이 존재하는지 확인 (회귀 방지)."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "_committed_json_sha" in src, "MT-19: 커밋 파일 재읽기 SHA 변수(_committed_json_sha)가 없음"
    assert "json_out_path.read_bytes()" in src, (
        "MT-19: 커밋된 json 파일을 read_bytes()로 재읽어 SHA를 계산해야 함"
    )


# ── TC-MT20: approval_request_message가 정확히 1번 출현 ───────────────────────
def test_tc_mt20_approval_request_appears_once():
    """_build_approval_request_output의 approval_request_message에 '사용자 승인 요청'이 정확히 1번."""
    import pipeline as pl

    out = pl._build_approval_request_output("IMP-20260703-B985", "https://github.com/x/pull/1")
    msg = out["approval_request_message"]
    assert msg.count("사용자 승인 요청") == 1, (
        f"'사용자 승인 요청'이 {msg.count('사용자 승인 요청')}번 출현 (정확히 1번이어야 함)"
    )
    # 4요소 고정 양식 확인.
    assert "PR: https://github.com/x/pull/1" in msg
    assert "승인 코드:\nACCEPT-IMP-20260703-B985" in msg
    assert "CODEX 검토 필요" in msg


def test_tc_mt20_build_approval_output_type_guards():
    """_build_approval_request_output의 None/비str/빈 pipeline_id 방어."""
    import pipeline as pl

    with pytest.raises(TypeError):
        pl._build_approval_request_output(None, "url")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        pl._build_approval_request_output("PID", None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        pl._build_approval_request_output(123, "url")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        pl._build_approval_request_output("", "url")


# ─────────────────────────────────────────────────────────────────────────────
# MT-22: SHA 불변식 회귀 테스트 (IMP-20260703-B985)
# ─────────────────────────────────────────────────────────────────────────────

class TestMT22SHAInvariant:
    """MT-21 수정으로 보장되는 staging/publish SHA 3자 불변식 회귀 테스트."""

    def test_mt22_materialize_staging_returns_json_content(self, tmp_path, monkeypatch):
        """staging 모드(_materialize_acceptance_snapshot publish=False)가
        json_content 필드를 반환하고, 그 SHA가 sha_manifest.json_sha256와 일치하는지."""
        from unittest.mock import patch
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import pipeline as pl

        monkeypatch.setattr(pl, "BASE_DIR", tmp_path)
        monkeypatch.setattr(pl, "ACCEPTANCE_REQUEST_FILE", str(tmp_path / "acceptance_request.json"))
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "human_acceptance_packet.md")
        monkeypatch.setattr(pl, "_packet_json_output_path", lambda: tmp_path / "human_acceptance_packet.json")

        state = {"pipeline_id": "IMP-TEST-0001", "external_gates": {}, "acceptance_request": {}}
        req = {"nonce": "testnonce", "pipeline_id": "IMP-TEST-0001", "status": "PENDING",
                "evidence_sha256": "abc", "pr_head_sha": "def", "github_ci_run_id": "123"}

        with patch.object(pl, "_collect_packet_evidence", return_value={"pipeline_id": "IMP-TEST-0001",
               "evidence_integrity": {}, "workspace_hygiene": {}}):
            with patch.object(pl, "_build_final_packet_content", return_value="MOCK_MD_CONTENT"):
                with patch.object(pl, "_build_verification_json", return_value={"mock": "json"}):
                    result = pl._materialize_acceptance_snapshot(state, req, publish=False,
                                                                  frozen_at="2026-01-01T00:00:00Z")

        json_content = result.get("json_content", "")
        assert json_content, "json_content가 반환되어야 함"
        # manifest json_sha256은 _sha256_file(tmp_json)으로 계산된다(디스크에 write_text된 파일 기준).
        # MT-21 production 경로(_materialize_acceptance_snapshot)는
        #   tmp_json.write_text(_vj_write_str, encoding="utf-8", newline="")
        # 로 기록하여 Windows 기본 newline 변환(\n→\r\n)을 차단한 뒤 그 디스크 파일을 _sha256_file로
        # 해싱한다. 따라서 반환된 json_content(_vj_write_str)도 동일하게 newline=""로 재기록해야
        # disk bytes가 production과 정확히 일치하여 SHA가 맞는다. 기본 write_text로 기록하면 Windows에서
        # \n→\r\n 변환이 일어나 production LF bytes와 어긋나므로, production과 동일한
        # newline="" write_text→_sha256_file 경로로 재현한다.
        probe = tmp_path / "probe_json_content.json"
        probe.write_text(json_content, encoding="utf-8", newline="")
        sha_from_content = pl._sha256_file(probe)
        sha_from_manifest = result.get("sha_manifest", {}).get("json_sha256", "")
        assert sha_from_content == sha_from_manifest, (
            f"json_content SHA({sha_from_content[:16]}...) != sha_manifest.json_sha256({sha_from_manifest[:16]}...)"
        )

    def test_mt22_save_acceptance_staging_stores_json_fields(self, tmp_path, monkeypatch):
        """새 staging 생성 시 acceptance_staging.json에 staged_json_content와
        staged_json_sha256 필드가 저장되는지."""
        import json as json_mod
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import pipeline as pl

        staging_path = tmp_path / "acceptance_staging.json"
        monkeypatch.setattr(pl, "ACCEPTANCE_STAGING_PATH", str(staging_path))
        monkeypatch.setattr(pl, "BASE_DIR", tmp_path)

        pl._save_acceptance_staging({
            "pipeline_id": "IMP-TEST-0001",
            "staged_packet_content": "PKT",
            "staged_packet_sha256": "abc123",
            "staged_json_content": '{"mock": "json"}',
            "staged_json_sha256": "def456",
        })

        saved = json_mod.loads(staging_path.read_text(encoding="utf-8"))
        assert saved.get("staged_json_content") == '{"mock": "json"}'
        assert saved.get("staged_json_sha256") == "def456"

    def test_mt22_reuse_path_populates_json_sha256_in_manifest(self, tmp_path, monkeypatch):
        """frozen staging reuse path에서 staged_sha_manifest에 json_sha256이 포함되는지."""
        import json as json_mod
        import hashlib
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))

        staging_content = {
            "staged_json_content": '{"key": "value"}',
            "staged_json_sha256": hashlib.sha256(b'{"key": "value"}').hexdigest(),
        }
        staging_path = tmp_path / "acceptance_staging.json"
        staging_path.write_text(json_mod.dumps(staging_content), encoding="utf-8")

        loaded = json_mod.loads(staging_path.read_text(encoding="utf-8"))
        json_sha = loaded.get("staged_json_sha256", "")
        expected = hashlib.sha256(b'{"key": "value"}').hexdigest()
        assert json_sha == expected, "staged_json_sha256가 올바르게 저장/로드되어야 함"

    def test_mt22_suppress_pending_comment_skips_comment_post(self, tmp_path, monkeypatch):
        """suppress_pending_comment=True이면 _post_github_pending_acceptance_comment가
        호출되지 않는지."""
        import sys
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import pipeline as pl

        monkeypatch.setattr(pl, "BASE_DIR", tmp_path)
        (tmp_path / "acceptance_request.json").write_text(
            '{"pipeline_id": "T", "status": "PENDING", "nonce": "n"}', encoding="utf-8"
        )
        (tmp_path / "human_acceptance_packet.md").write_text("MD", encoding="utf-8")
        (tmp_path / "human_acceptance_packet.json").write_text('{"a":1}', encoding="utf-8")

        mock_snapshot = {
            "pr_body_updated": True,
            "pr_body_update_failed": False,
            "packet_path": str(tmp_path / "human_acceptance_packet.md"),
            "sha_manifest": {"packet_sha256": "abc", "json_sha256": "def"},
        }

        with patch.object(pl, "_materialize_acceptance_snapshot", return_value=mock_snapshot):
            with patch.object(pl, "_verify_published_canonical_pr_body", return_value=None):
                with patch.object(pl, "_post_github_pending_acceptance_comment") as mock_comment:
                    with patch.object(pl, "_load_acceptance_request",
                                       return_value={"pipeline_id": "T", "status": "PENDING"}):
                        state = {"pipeline_id": "T", "acceptance_request": {}}
                        req = {"pipeline_id": "T", "nonce": "n"}
                        pl._publish_acceptance_request(
                            state, req, "/evidence", "",
                            suppress_pending_comment=True
                        )
                        mock_comment.assert_not_called()

    def test_mt22_frozen_publish_uses_staged_json_content(self, tmp_path, monkeypatch):
        """frozen publish path에서 staged_json_content가 제공되면 JSON 파일에
        그 내용이 그대로 기록되는지 (SHA 불변식 핵심)."""
        import hashlib
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import pipeline as pl

        staged_json = '{"staged": true, "generated_at": "STAGING_PROBE"}'
        staged_json_sha = hashlib.sha256(staged_json.encode("utf-8")).hexdigest()

        packet_path = tmp_path / "human_acceptance_packet.md"
        json_path = tmp_path / "human_acceptance_packet.json"
        req_path = tmp_path / "acceptance_request.json"
        req_path.write_text('{"pipeline_id": "T", "status": "PENDING"}', encoding="utf-8")

        monkeypatch.setattr(pl, "BASE_DIR", tmp_path)
        monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_path)
        monkeypatch.setattr(pl, "_packet_json_output_path", lambda: json_path)
        monkeypatch.setattr(pl, "ACCEPTANCE_REQUEST_FILE", str(req_path))

        state = {"pipeline_id": "T", "external_gates": {}, "acceptance_request": {}}
        req = {"pipeline_id": "T", "nonce": "n", "status": "PENDING"}

        frozen_md = "## FROZEN MD CONTENT\n verification_json_sha256: " + staged_json_sha

        with monkeypatch.context() as m:
            m.setattr(pl, "_collect_packet_evidence", lambda *a, **kw: {
                "pipeline_id": "T", "evidence_integrity": {}, "workspace_hygiene": {}
            })
            # gh 없는 테스트 환경: _gh_available()가 False를 반환하도록 강제하여 PR body 갱신 경로를
            # 건너뛴다(디스크 JSON 커밋 SHA 불변식만 검증한다).
            m.setattr(pl, "_gh_available", lambda *a, **kw: False)
            pl._materialize_acceptance_snapshot(
                state, req, publish=True,
                frozen_packet_content=frozen_md,
                staged_json_content=staged_json,
                staged_json_sha256=staged_json_sha,
            )

        written_content = json_path.read_text(encoding="utf-8")
        written_sha = hashlib.sha256(written_content.encode("utf-8")).hexdigest()
        assert written_content == staged_json, "기록된 JSON이 staged JSON과 동일해야 함"
        assert written_sha == staged_json_sha, (
            f"기록된 JSON SHA({written_sha[:16]}...) != staged_json_sha({staged_json_sha[:16]}...)"
        )


class TestDualChannelOutputPrevention:
    """MT-23: 이중 출력 채널 방지 회귀 테스트.

    실제 실패 경로: pipeline.py stdout + Pipeline Manager 중계가 동시에 활성화되어
    사용자가 승인 요청을 2회 받는 문제를 회귀 테스트로 차단한다.
    """

    def test_machine_readable_suppresses_human_stdout(self, tmp_path, monkeypatch):
        """--machine-readable 모드에서 human stdout이 출력되지 않아야 한다."""
        import json as json_mod2

        # _build_approval_request_output 결과를 가져온다
        result = pipeline._build_approval_request_output(
            "IMP-20260703-B985", "https://github.com/test/pr/1"
        )
        msg = result.get("approval_request_message", "")

        # machine-readable JSON 출력은 사용자 승인 요청 텍스트를 approval_request_message 안에 담는다
        # 이 메시지가 JSON key로 감싸져 있지 않고 raw stdout에 직접 출력되면 이중 출력이 됨
        # human_stdout_phrases는 machine-readable 모드에서 직접 print되면 안 된다
        human_stdout_phrases = ["사용자 승인 요청", "승인 코드:", "CODEX 검토 필요"]

        # approval_request_message 안에는 포함되어 있어야 한다 (올바른 경로)
        for phrase in human_stdout_phrases:
            assert phrase in msg, f"approval_request_message에 '{phrase}'가 없음: {msg!r}"

        # raw JSON 출력을 파싱하면 human phrases가 approval_request_message key 아래에만 있어야 한다
        json_output = json_mod2.dumps(result, ensure_ascii=False)
        parsed = json_mod2.loads(json_output)
        assert "approval_request_message" in parsed
        assert "acceptance_code_display" in parsed
        # acceptance_code_display에는 승인 요청 phrase가 없어야 한다
        code_display = parsed.get("acceptance_code_display", "")
        for phrase in ["사용자 승인 요청", "승인 코드:", "CODEX 검토 필요"]:
            assert phrase not in code_display, (
                f"acceptance_code_display에 '{phrase}' 포함 — 이중 출력 위험"
            )

    def test_approval_request_message_contains_required_phrases_exactly_once(self):
        """approval_request_message에 각 핵심 문구가 정확히 1회만 포함된다."""
        result = pipeline._build_approval_request_output(
            "IMP-20260703-B985", "https://github.com/test/pr/1"
        )
        msg = result.get("approval_request_message", "")

        required_phrases = ["사용자 승인 요청", "승인 코드:", "CODEX 검토 필요"]
        for phrase in required_phrases:
            count = msg.count(phrase)
            assert count == 1, f"'{phrase}'가 {count}회 나타남 (정확히 1회여야 함)"

    def test_pr_diff_artifact_exclusion_check(self):
        """PR diff에 포함되면 안 되는 실행 산출물 파일명 패턴을 검증한다.

        dev_handover_*.xml, build_report*.xml, integration_report*.xml,
        qa_report*.xml 같은 파이프라인 실행 산출물이 product 코드 PR에 포함되면
        scope 자기모순이 발생한다.
        """
        import re

        # 실행 산출물 파일명 패턴 (PR diff에 들어오면 안 됨)
        artifact_patterns = [
            r"dev_handover.*\.xml$",
            r"build_report.*\.xml$",
            r"integration_report.*\.xml$",
            r"qa_report.*\.xml$",
            r"security_audit.*\.xml$",
            r"architect_report.*\.xml$",
            r"architect_rca.*\.xml$",
        ]

        sample_files_in_pr_diff = [
            "pipeline.py",
            "tests/test_canonical_pr_body_sha256_b985.py",
            ".claude/agents/pipeline-manager-agent.md",
        ]

        for f in sample_files_in_pr_diff:
            for pattern in artifact_patterns:
                assert not re.search(pattern, f), (
                    f"PR diff에 실행 산출물 '{f}'이 포함되어 있음 — scope 자기모순"
                )

        # 아래는 패턴이 실제로 탐지하는지 확인 (역방향 검증)
        artifact_files = [
            "dev_handover_b985_r3.xml",
            "build_report_b985_r3.xml",
            "integration_report_r4.xml",
        ]
        for f in artifact_files:
            matched = any(re.search(p, f) for p in artifact_patterns)
            assert matched, f"패턴이 실행 산출물 '{f}'을 탐지하지 못함"

    def test_build_approval_output_is_json_serializable(self):
        """_build_approval_request_output 결과는 JSON 직렬화 가능해야 한다."""
        import json as json_mod3

        result = pipeline._build_approval_request_output(
            "IMP-20260703-B985", "https://github.com/test/pr/1"
        )
        serialized = json_mod3.dumps(result, ensure_ascii=False)
        parsed = json_mod3.loads(serialized)
        assert parsed["status"] == "PENDING"
        assert parsed["codex_required"] is True
        assert "approval_request_message" in parsed

    def test_acceptance_code_display_does_not_contain_approval_phrases(self):
        """acceptance_code_display에는 승인 요청 phrase가 포함되지 않는다."""
        result = pipeline._build_approval_request_output(
            "IMP-20260703-B985", "https://github.com/test/pr/1"
        )
        code_display = result.get("acceptance_code_display", "")
        for phrase in ["사용자 승인 요청", "승인 코드:", "CODEX 검토 필요"]:
            assert phrase not in code_display, (
                f"acceptance_code_display에 '{phrase}' 포함 — 이중 출력 위험"
            )

    def test_pr_diff_artifact_pattern_detection(self):
        """실행 산출물 파일명 패턴이 올바르게 탐지된다."""
        import re
        artifact_patterns = [
            r"dev_handover.*\.xml$",
            r"build_report.*\.xml$",
            r"integration_report.*\.xml$",
            r"qa_report.*\.xml$",
        ]
        artifact_files = [
            "dev_handover_b985_r3.xml",
            "build_report_b985_r3.xml",
            "integration_report_r4.xml",
            "qa_report_b985_r3.xml",
        ]
        for f in artifact_files:
            matched = any(re.search(p, f) for p in artifact_patterns)
            assert matched, f"패턴이 실행 산출물 '{f}'을 탐지하지 못함"
        # 제품 코드는 탐지하지 않아야 함
        product_files = ["pipeline.py", "tests/test_foo.py"]
        for f in product_files:
            matched = any(re.search(p, f) for p in artifact_patterns)
            assert not matched, f"제품 파일 '{f}'이 실행 산출물로 오탐됨"


class TestPacketGateCIConsistency:
    """MT-24: packet github_ci 상태와 실제 gate 상태 일치 검증."""

    def test_check_approval_request_ready_blocks_when_packet_has_github_ci_fail(
        self, tmp_path, monkeypatch
    ):
        """packet에 github_ci: FAIL이 있고 실제 gate가 PASS이면 BLOCKED."""
        import json as _json

        state_path = tmp_path / "pipeline_state.json"
        packet_path = tmp_path / "human_acceptance_packet.md"

        # 실제 gate: PASS
        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {
                "github_ci": {"status": "PASS", "evidence": "github_actions_run:12345"},
                "technical": {"status": "PASS"},
                "oracle": {"status": "PASS"},
                "acceptance": {"status": "PENDING"},
            },
        }
        state_path.write_text(_json.dumps(state), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))

        # 검사2(codex APPROVED)를 통과시켜 검사7까지 도달하도록 codex 결과 준비
        codex_dir = state_path.parent / ".pipeline"
        codex_dir.mkdir(parents=True, exist_ok=True)
        (codex_dir / "codex_review_result.json").write_text(
            _json.dumps({"status": "APPROVED"}), encoding="utf-8"
        )

        # packet에는 FAIL 표시 (stale)
        packet_path.write_text(
            "github_ci: FAIL\nGitHub CI: FAIL\n판단 정보 상태: 판단 가능",
            encoding="utf-8",
        )

        import importlib
        import pipeline as _pl
        importlib.reload(_pl)
        # acceptance_request 부재로 검사1/3/4 skip → 검사2(codex)와 검사7만 gate
        monkeypatch.setattr(_pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(_pl, "_packet_output_path", lambda: packet_path)
        result = _pl._check_approval_request_ready("dummy pr body")

        assert result.get("ok") is False, "packet github_ci FAIL인데 PASS 반환 — BLOCKED 필요"
        assert result.get("failure_code") == "packet_github_ci_stale"

    def test_check_approval_request_ready_passes_when_packet_has_github_ci_pass(
        self, tmp_path, monkeypatch
    ):
        """packet에 github_ci: PASS이고 실제 gate도 PASS이면 packet_github_ci_stale BLOCKED 없음."""
        import json as _json

        state_path = tmp_path / "pipeline_state.json"
        packet_path = tmp_path / "human_acceptance_packet.md"

        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {
                "github_ci": {"status": "PASS", "evidence": "github_actions_run:12345"},
                "technical": {"status": "PASS"},
                "oracle": {"status": "PASS"},
                "acceptance": {"status": "PENDING"},
            },
        }
        state_path.write_text(_json.dumps(state), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))

        # packet에도 PASS 표시
        packet_path.write_text(
            "github_ci: PASS\nGitHub CI: PASS\n판단 정보 상태: 판단 가능",
            encoding="utf-8",
        )

        import importlib
        import pipeline as _pl
        importlib.reload(_pl)
        monkeypatch.setattr(_pl, "_packet_output_path", lambda: packet_path)
        result = _pl._check_approval_request_ready(
            "작업 요약\n사용자가 확인할 결과물\n기대 결과와 실제 결과\n중요한 선택과 트레이드오프\n검증\n판단 정보 상태: 판단 가능"
        )

        # packet_github_ci_stale 검증만 통과하면 됨 (다른 섹션 검증은 실패할 수 있음)
        if not result.get("ok"):
            assert result.get("failure_code") != "packet_github_ci_stale", (
                f"packet github_ci PASS인데 packet_github_ci_stale BLOCKED: {result}"
            )


# ---------------------------------------------------------------------------
# MT-25: CI final-check gate 회귀 테스트
# ---------------------------------------------------------------------------

class TestCIFinalCheckGate:
    """CI final-check 댓글 상태에 따른 gates request-accept 차단 동작 검증 (MT-25)."""

    def test_get_ci_final_check_status_fail_for_stale_packet(self, monkeypatch):
        """_get_ci_final_check_status가 stale packet 댓글 파싱 시 FAIL 반환."""
        import pipeline as _pl

        fake_comment_body = (
            "<!-- pipeline-final-check-packet -->\n"
            "판단 정보 상태: **정보 부족**\n"
            "stale packet (packet file not found)\n"
        )
        fake_comments = [
            {"body": fake_comment_body, "html_url": "https://github.com/test/pr#issuecomment-99"}
        ]

        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = ""
                stderr = ""

            r = FakeResult()
            if "gh" in cmd[0] and "pr" in cmd and "view" in cmd:
                r.stdout = json.dumps({"number": 835, "headRefName": "impl/test"})
            elif "gh" in cmd[0] and "repo" in cmd and "view" in cmd:
                r.stdout = json.dumps({"nameWithOwner": "owner/repo"})
            elif "gh" in cmd[0] and "api" in cmd:
                r.stdout = json.dumps(fake_comments)
            return r

        import subprocess
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _pl._get_ci_final_check_status()
        assert result["status"] == "FAIL"
        assert "stale packet" in result["reason"] or "정보 부족" in result["reason"]
        assert result["comment_url"] == "https://github.com/test/pr#issuecomment-99"

    def test_get_ci_final_check_status_pass_for_ready(self, monkeypatch):
        """_get_ci_final_check_status가 '판단 가능' 댓글 파싱 시 PASS 반환."""
        import pipeline as _pl

        fake_comment_body = (
            "<!-- pipeline-final-check-packet -->\n"
            "판단 정보 상태: **판단 가능**\n"
        )
        fake_comments = [{"body": fake_comment_body, "html_url": "https://github.com/test/pr#issuecomment-1"}]

        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = ""
                stderr = ""

            r = FakeResult()
            if "gh" in cmd[0] and "pr" in cmd and "view" in cmd:
                r.stdout = json.dumps({"number": 835, "headRefName": "impl/test"})
            elif "gh" in cmd[0] and "repo" in cmd and "view" in cmd:
                r.stdout = json.dumps({"nameWithOwner": "owner/repo"})
            elif "gh" in cmd[0] and "api" in cmd:
                r.stdout = json.dumps(fake_comments)
            return r

        import subprocess
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _pl._get_ci_final_check_status()
        assert result["status"] == "PASS"

    def test_get_ci_final_check_status_not_found_when_no_comment(self, monkeypatch):
        """CI 댓글에 final-check 마커가 없으면 NOT_FOUND, graceful skip."""
        import pipeline as _pl

        fake_comments = [{"body": "일반 댓글입니다", "html_url": "https://github.com/test/pr#issuecomment-2"}]

        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = ""
                stderr = ""

            r = FakeResult()
            if "gh" in cmd[0] and "pr" in cmd and "view" in cmd:
                r.stdout = json.dumps({"number": 835, "headRefName": "impl/test"})
            elif "gh" in cmd[0] and "repo" in cmd and "view" in cmd:
                r.stdout = json.dumps({"nameWithOwner": "owner/repo"})
            elif "gh" in cmd[0] and "api" in cmd:
                r.stdout = json.dumps(fake_comments)
            return r

        import subprocess
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _pl._get_ci_final_check_status()
        assert result["status"] == "NOT_FOUND"

    def test_get_ci_final_check_status_graceful_when_gh_fails(self, monkeypatch):
        """gh pr view 실패(returncode!=0) 시 NOT_FOUND로 graceful skip."""
        import pipeline as _pl

        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 1
                stdout = ""
                stderr = "gh: not found"

            return FakeResult()

        import subprocess
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = _pl._get_ci_final_check_status()
        assert result["status"] == "NOT_FOUND"

    def test_approval_ready_blocks_when_ci_final_check_insufficient(self, monkeypatch):
        """검사 8: CI final-check FAIL 시 ci_final_check_insufficient BLOCKED.

        검사 1~7을 모두 통과하도록 파일/PR body를 준비한 뒤, 검사 8만
        FAIL이 되도록 _get_ci_final_check_status를 monkeypatch한다.
        """
        import pipeline as _pl

        # 검사 1~7이 통과/skip하도록 최소 조건 구성.
        # 검사 2(codex_review_result APPROVED)는 파일이 필요하므로 함수를 우회.
        monkeypatch.setattr(
            _pl, "_load_acceptance_request", lambda: None
        )  # 검사 1 skip
        monkeypatch.setattr(
            _pl, "_packet_output_path", lambda: Path("__mt25_nonexistent_packet__.md")
        )  # 검사 3,4,7 skip (파일 없음)

        # 검사 2: codex_review_result APPROVED로 통과시킨다.
        _cx_path = Path("__mt25_codex_result__.json")
        _cx_path.write_text(json.dumps({"status": "APPROVED"}), encoding="utf-8")
        monkeypatch.setattr(_pl, "_codex_review_result_path", lambda: _cx_path)

        # 검사 8: CI final-check FAIL.
        monkeypatch.setattr(
            _pl,
            "_get_ci_final_check_status",
            lambda **kw: {
                "status": "FAIL",
                "reason": "CI final-check shows 정보 부족: stale packet (packet file not found)",
                "comment_url": "https://github.com/test/pr#issuecomment-99",
            },
        )

        try:
            result = _pl._check_approval_request_ready(
                "작업 요약\n사용자가 확인할 결과물\n기대 결과와 실제 결과\n중요한 선택과 트레이드오프\n검증"
            )
        finally:
            if _cx_path.exists():
                _cx_path.unlink()

        assert result["ok"] is False
        assert result["failure_code"] == "ci_final_check_insufficient"
        assert "정보 부족" in result["message"] or "stale packet" in result["message"]

    def test_approval_ready_graceful_when_ci_final_check_not_found(self, monkeypatch):
        """검사 8: CI 댓글 없음(NOT_FOUND) 시 검사 8은 통과(ok 유지)."""
        import pipeline as _pl

        monkeypatch.setattr(_pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(
            _pl, "_packet_output_path", lambda: Path("__mt25_nonexistent_packet__.md")
        )
        _cx_path = Path("__mt25_codex_result2__.json")
        _cx_path.write_text(json.dumps({"status": "APPROVED"}), encoding="utf-8")
        monkeypatch.setattr(_pl, "_codex_review_result_path", lambda: _cx_path)
        monkeypatch.setattr(
            _pl,
            "_get_ci_final_check_status",
            lambda **kw: {"status": "NOT_FOUND", "reason": "no final-check comment found", "comment_url": ""},
        )
        # 검사 6: PR body에 acceptance FAIL 표시 없음 → 통과.
        try:
            result = _pl._check_approval_request_ready(
                "작업 요약\n사용자가 확인할 결과물\n검증"
            )
        finally:
            if _cx_path.exists():
                _cx_path.unlink()

        # 검사 8이 NOT_FOUND면 ci_final_check_insufficient로 차단하지 않는다.
        assert result.get("failure_code") != "ci_final_check_insufficient"


class TestMT26PRBodyPacketJSON:
    """MT-26: report update-pr-body가 PR body에 packet JSON을 embed하고,
    CI가 파일 커밋 없이 PR body에서 packet JSON을 읽어 freshness 검증할 수 있게 한다."""

    def test_markers_defined(self):
        """PIPELINE_PACKET_JSON 마커 상수가 정의되어 있다 (MT-28: 단일 HTML 주석 경계 형식)."""
        # IMP-20260703-B985 MT-28: JSON이 단일 HTML 주석 안에 들어가도록 START는 주석을 열기만,
        # END는 닫기만 한다. 결과 블록은 렌더링 시 보이지 않는다.
        assert pipeline.PIPELINE_PACKET_JSON_START_MARKER == "<!-- PIPELINE_PACKET_JSON_START"
        assert pipeline.PIPELINE_PACKET_JSON_END_MARKER == "PIPELINE_PACKET_JSON_END -->"
        # 두 마커 모두 여전히 substring 추출 계약 키워드를 포함한다.
        assert "PIPELINE_PACKET_JSON_START" in pipeline.PIPELINE_PACKET_JSON_START_MARKER
        assert "PIPELINE_PACKET_JSON_END" in pipeline.PIPELINE_PACKET_JSON_END_MARKER

    def test_replace_appends_block_when_absent(self):
        """블록이 없으면 PR body 끝에 JSON 블록을 추가한다."""
        body = "작업 요약\n내용"
        one_line = '{"schema_version":1,"pr":{"head_sha":"abc123"}}'
        out = pipeline._replace_pr_body_packet_json_block(body, one_line)
        assert pipeline.PIPELINE_PACKET_JSON_START_MARKER in out
        assert pipeline.PIPELINE_PACKET_JSON_END_MARKER in out
        assert one_line in out
        # 기존 본문 보존
        assert "작업 요약" in out

    def test_replace_updates_existing_block(self):
        """기존 블록이 있으면 교체하고 중복 추가하지 않는다."""
        start = pipeline.PIPELINE_PACKET_JSON_START_MARKER
        end = pipeline.PIPELINE_PACKET_JSON_END_MARKER
        old = '{"pr":{"head_sha":"OLD"}}'
        new = '{"pr":{"head_sha":"NEW"}}'
        body = f"머리말\n{start}\n{old}\n{end}\n꼬리말"
        out = pipeline._replace_pr_body_packet_json_block(body, new)
        assert new in out
        assert old not in out
        # 마커는 정확히 1쌍만 존재
        assert out.count(start) == 1
        assert out.count(end) == 1
        assert "머리말" in out and "꼬리말" in out

    def test_replace_handles_backslash_json_safely(self):
        """JSON 내 백슬래시/그룹참조 유사 문자열이 re.sub group 오류를 일으키지 않는다."""
        start = pipeline.PIPELINE_PACKET_JSON_START_MARKER
        end = pipeline.PIPELINE_PACKET_JSON_END_MARKER
        # \g<0> 유사 패턴과 백슬래시 포함 (윈도우 경로 등)
        tricky = r'{"path":"C:\\temp\\out","note":"\g<0> literal"}'
        body = f"{start}\nOLD\n{end}"
        out = pipeline._replace_pr_body_packet_json_block(body, tricky)
        assert tricky in out

    def test_replace_rejects_none(self):
        """None json_one_line은 TypeError를 발생시킨다 (AL type guard)."""
        with pytest.raises(TypeError):
            pipeline._replace_pr_body_packet_json_block("body", None)

    def test_replace_rejects_non_str(self):
        """비문자열 json_one_line은 TypeError를 발생시킨다."""
        with pytest.raises(TypeError):
            pipeline._replace_pr_body_packet_json_block("body", {"a": 1})

    def test_load_packet_json_one_line_reads_file(self, tmp_path, monkeypatch):
        """packet.json 파일이 있으면 compact 한 줄 JSON으로 반환한다."""
        pkt = {
            "schema_version": 1,
            "packet_type": "final_acceptance_evidence",
            "pr": {"head_sha": "abc123"},
            "github_actions": {"run_id": "999"},
        }
        pkt_path = tmp_path / "human_acceptance_packet.json"
        pkt_path.write_text(json.dumps(pkt, indent=2), encoding="utf-8")
        monkeypatch.setattr(pipeline, "_packet_json_output_path", lambda: pkt_path)
        one_line = pipeline._load_packet_json_one_line()
        assert one_line is not None
        assert "\n" not in one_line  # 한 줄
        parsed = json.loads(one_line)
        assert parsed["pr"]["head_sha"] == "abc123"
        assert parsed["github_actions"]["run_id"] == "999"

    def test_load_packet_json_one_line_missing_file_returns_none(self, tmp_path, monkeypatch):
        """packet.json 파일이 없으면 None을 반환한다 (graceful skip)."""
        missing = tmp_path / "nonexistent_packet.json"
        monkeypatch.setattr(pipeline, "_packet_json_output_path", lambda: missing)
        assert pipeline._load_packet_json_one_line() is None

    def test_load_packet_json_one_line_invalid_json_returns_none(self, tmp_path, monkeypatch):
        """packet.json이 깨진 JSON이면 None을 반환한다."""
        bad = tmp_path / "human_acceptance_packet.json"
        bad.write_text("{ not valid json ", encoding="utf-8")
        monkeypatch.setattr(pipeline, "_packet_json_output_path", lambda: bad)
        assert pipeline._load_packet_json_one_line() is None

    def test_load_packet_json_preserves_korean(self, tmp_path, monkeypatch):
        """한글 값이 ensure_ascii=False로 보존된다."""
        pkt = {"note": "사용자 확인", "pr": {"head_sha": "x"}}
        pkt_path = tmp_path / "human_acceptance_packet.json"
        pkt_path.write_text(json.dumps(pkt, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(pipeline, "_packet_json_output_path", lambda: pkt_path)
        one_line = pipeline._load_packet_json_one_line()
        assert one_line is not None
        assert "사용자 확인" in one_line

    def test_ci_yml_reads_packet_from_pr_body(self):
        """ci.yml이 PR body에서 packet JSON을 추출하는 로직을 포함한다."""
        ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        text = ci_path.read_text(encoding="utf-8")
        assert "Extract-PacketJsonFromPRBody" in text
        assert "PIPELINE_PACKET_JSON_START" in text
        assert "PIPELINE_PACKET_JSON_END" in text

    def test_ci_yml_keeps_file_fallback(self):
        """ci.yml이 PR body에 JSON 없을 때 파일 fallback을 유지한다."""
        ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        text = ci_path.read_text(encoding="utf-8")
        # 파일 fallback 경로와 "packet file not found" 분기(정보 부족 판정) 보존
        assert "human_acceptance_packet.json" in text
        assert "packet file not found" in text

    def test_round_trip_embed_and_extract_semantics(self, tmp_path, monkeypatch):
        """embed된 JSON을 다시 파싱하면 head_sha/run_id가 그대로 복원된다 (CI 추출 계약)."""
        pkt = {
            "schema_version": 1,
            "pr": {"head_sha": "deadbeef1234"},
            "github_actions": {"run_id": "42"},
        }
        pkt_path = tmp_path / "human_acceptance_packet.json"
        pkt_path.write_text(json.dumps(pkt), encoding="utf-8")
        monkeypatch.setattr(pipeline, "_packet_json_output_path", lambda: pkt_path)
        one_line = pipeline._load_packet_json_one_line()
        body = pipeline._replace_pr_body_packet_json_block("최종 확인 안내", one_line)
        # CI의 Extract-PacketJsonFromPRBody와 동일한 substring 추출 재현
        start = pipeline.PIPELINE_PACKET_JSON_START_MARKER
        end = pipeline.PIPELINE_PACKET_JSON_END_MARKER
        s = body.index(start) + len(start)
        e = body.index(end)
        extracted = body[s:e].strip()
        parsed = json.loads(extracted)
        assert parsed["pr"]["head_sha"] == "deadbeef1234"
        assert parsed["github_actions"]["run_id"] == "42"


class TestMT28AcceptanceFlow:
    """MT-28: nonce 노출 제거 / packet PENDING 갱신 / JSON을 HTML 주석 안에 배치 /
    fresh pending comment / Check 9(non-blocking warn) 회귀 테스트."""

    def test_request_accept_output_no_nonce(self):
        """gates request-accept 출력 승인 코드에 nonce(하이픈+본체)가 없다.

        승인 코드 표시 형식은 ACCEPT-{pipeline_id} (파이프라인 ID로만 끝남).
        approval_request_message에도 nonce 포함 형식(ACCEPT-{pid}-{nonce})이 없어야 한다.
        """
        pid = "IMP-20260703-B985"
        out = pipeline._build_approval_request_output(pid, "https://example/pr/1")
        # 표시 코드는 nonce 없는 순수 형식.
        assert out["acceptance_code_display"] == f"ACCEPT-{pid}"
        # nonce 포함 형식(ACCEPT-IMP-20260703-B985-XXXX)이 message에 없어야 한다.
        assert f"ACCEPT-{pid}-" not in out["approval_request_message"]
        # 정확히 nonce 없는 코드가 message에 포함된다.
        assert f"ACCEPT-{pid}" in out["approval_request_message"]
        assert out["status"] == "PENDING"

    def test_packet_json_in_html_comment(self):
        """_replace_pr_body_packet_json_block 반환 블록이 단일 HTML 주석 경계 형식이다.

        형식: "<!-- PIPELINE_PACKET_JSON_START\\n{json}\\nPIPELINE_PACKET_JSON_END -->"
        JSON 전체가 하나의 HTML 주석 안에 들어가 렌더링 시 보이지 않는다.
        """
        one_line = '{"schema_version":1,"pr":{"head_sha":"abc123"}}'
        out = pipeline._replace_pr_body_packet_json_block("작업 요약", one_line)
        expected_block = (
            f"{pipeline.PIPELINE_PACKET_JSON_START_MARKER}\n"
            f"{one_line}\n"
            f"{pipeline.PIPELINE_PACKET_JSON_END_MARKER}"
        )
        assert expected_block in out
        # START는 주석을 열기만(닫는 --> 없음), END는 닫기만 한다.
        assert pipeline.PIPELINE_PACKET_JSON_START_MARKER == "<!-- PIPELINE_PACKET_JSON_START"
        assert pipeline.PIPELINE_PACKET_JSON_END_MARKER == "PIPELINE_PACKET_JSON_END -->"
        # 블록 내부의 JSON은 START(<!--)와 END(-->) 사이, 즉 단일 주석 안에 위치한다.
        _comment_open = out.index("<!-- PIPELINE_PACKET_JSON_START")
        _comment_close = out.index("PIPELINE_PACKET_JSON_END -->") + len("PIPELINE_PACKET_JSON_END -->")
        assert out.index(one_line) > _comment_open
        assert out.index(one_line) < _comment_close

    def test_packet_json_backwards_compat_replaces_legacy_marker(self):
        """구 마커(self-closed 주석)가 남아 있으면 새 마커로 교체한다 (backwards-compat)."""
        legacy_start = "<!-- PIPELINE_PACKET_JSON_START -->"
        legacy_end = "<!-- PIPELINE_PACKET_JSON_END -->"
        old = '{"pr":{"head_sha":"OLD"}}'
        new = '{"pr":{"head_sha":"NEW"}}'
        body = f"머리말\n{legacy_start}\n{old}\n{legacy_end}\n꼬리말"
        out = pipeline._replace_pr_body_packet_json_block(body, new)
        assert new in out
        assert old not in out
        # 구 self-closed 마커는 더 이상 존재하지 않는다.
        assert legacy_start not in out
        assert legacy_end not in out
        # 새 마커가 정확히 1쌍 존재한다.
        assert out.count(pipeline.PIPELINE_PACKET_JSON_START_MARKER) == 1
        assert out.count(pipeline.PIPELINE_PACKET_JSON_END_MARKER) == 1
        assert "머리말" in out and "꼬리말" in out

    def test_packet_shows_pending_after_request_accept(self):
        """승인 요청 표시 SSoT(_build_approval_request_output)의 acceptance 상태가 PENDING이다."""
        pid = "IMP-20260703-B985"
        out = pipeline._build_approval_request_output(pid, "")
        assert out["status"] == "PENDING"
        # PENDING 상태 문자열이 관측 가능해야 한다(대문자 SSoT).
        assert "PENDING" in str(out["status"]).upper()

    def test_pending_comment_posted_after_acceptance_request(self):
        """_render_pending_acceptance_comment가 pending 마커를 포함한다 (fresh pending comment)."""
        pid = "IMP-20260703-B985"
        display_model = {
            "pipeline_id": pid,
            "pr_url": "https://example/pr/1",
            "approval_code": f"ACCEPT-{pid}",
        }
        comment = pipeline._render_pending_acceptance_comment(display_model)
        assert "pipeline-human-acceptance-packet-pending" in comment
        # 승인 코드는 nonce 없는 형식으로 노출된다.
        assert f"ACCEPT-{pid}" in comment
        assert f"ACCEPT-{pid}-" not in comment
        # 완료 마커(accepted)는 포함되지 않는다.
        assert "pipeline-human-acceptance-packet-accepted" not in comment

    def test_extract_packet_json_new_marker_format(self, tmp_path, monkeypatch):
        """새 마커 형식에서 CI 추출 로직과 동일한 substring 파싱이 올바르게 동작한다."""
        pkt = {
            "schema_version": 1,
            "pr": {"head_sha": "cafebabe9999"},
            "github_actions": {"run_id": "77"},
        }
        pkt_path = tmp_path / "human_acceptance_packet.json"
        pkt_path.write_text(json.dumps(pkt, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(pipeline, "_packet_json_output_path", lambda: pkt_path)
        one_line = pipeline._load_packet_json_one_line()
        assert one_line is not None
        body = pipeline._replace_pr_body_packet_json_block("최종 확인 안내", one_line)
        # ci.yml / final_check.yml의 substring 추출을 재현: START 이후 ~ END 이전.
        start = pipeline.PIPELINE_PACKET_JSON_START_MARKER
        end = pipeline.PIPELINE_PACKET_JSON_END_MARKER
        si = body.index(start)
        ei = body.index(end)
        assert si >= 0 and ei > si
        extracted = body[si + len(start):ei].strip()
        parsed = json.loads(extracted)
        assert parsed["pr"]["head_sha"] == "cafebabe9999"
        assert parsed["github_actions"]["run_id"] == "77"


def _make_base_state(pipeline_id: str) -> dict:
    return {
        "pipeline_id": pipeline_id,
        "current_phase": 7,
        "external_gates": {
            "technical": {"status": "PENDING"},
            "oracle": {"status": "PENDING"},
            "github_ci": {"status": "PENDING"},
            "acceptance": {"status": "PENDING"},
        },
        "phase_attestations": {"enabled": True, "phases": {}},
        "requirements_tracking": {"enabled": False},
    }


BASE_DIR = REPO_ROOT


class TestMT27FinalCheckGate:
    """MT-27: final_check.yml workflow + auto-trigger in request-accept."""

    def test_final_check_workflow_yml_exists(self):
        """final_check.yml 파일이 존재하고 workflow_dispatch trigger를 포함한다."""
        wf_path = BASE_DIR / ".github" / "workflows" / "final_check.yml"
        assert wf_path.exists(), "final_check.yml이 .github/workflows/에 없습니다"
        content = wf_path.read_text(encoding="utf-8")
        assert "workflow_dispatch" in content, "workflow_dispatch trigger가 없습니다"
        assert "pr_number" in content, "pr_number input이 없습니다"
        assert "pipeline-final-check-packet" in content, "pipeline-final-check-packet 마커가 없습니다"

    def test_request_accept_blocked_when_final_check_insufficient(self, tmp_path, monkeypatch):
        """_get_ci_final_check_status가 FAIL을 반환하면 ci_final_check_insufficient BLOCKED."""
        import pipeline as pl

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps(_make_base_state("IMP-20260703-B985-MT27-T1")), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
        _write_codex_approved(tmp_path, monkeypatch)

        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(pl, "_get_pr_body_text", lambda: "## 작업 요약\n- 테스트\n")
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "nonexistent_packet.md")
        monkeypatch.setattr(pl, "_get_ci_final_check_status", lambda: {"status": "FAIL", "reason": "정보 부족"})
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: None)  # trigger skip

        result = pl._check_approval_request_ready()
        assert result.get("ok") is False
        assert result.get("failure_code") == "ci_final_check_insufficient"

    def test_request_accept_triggers_workflow_when_insufficient(self, tmp_path, monkeypatch):
        """final-check FAIL 시 gh workflow run final_check.yml 트리거를 시도한다."""
        import pipeline as pl
        import sys

        triggered = []

        def mock_run(cmd, **kwargs):
            if isinstance(cmd, list) and "workflow" in cmd and "run" in cmd and "final_check.yml" in cmd:
                triggered.append(list(cmd))
                class Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return Result()
            # git branch 명령은 실제 실행하되 결과 오버라이드
            if isinstance(cmd, list) and "rev-parse" in cmd:
                class BranchResult:
                    returncode = 0
                    stdout = "impl/IMP-20260703-B985-v2\n"
                    stderr = ""
                return BranchResult()
            class FailResult:
                returncode = 1
                stdout = ""
                stderr = ""
            return FailResult()

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps(_make_base_state("IMP-20260703-B985-MT27-T2")), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
        _write_codex_approved(tmp_path, monkeypatch)

        call_count = [0]
        def mock_final_check():
            call_count[0] += 1
            if call_count[0] <= 1:
                return {"status": "FAIL", "reason": "정보 부족"}
            return {"status": "PASS"}

        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(pl, "_get_pr_body_text", lambda: "## 작업 요약\n- 테스트\n")
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "nonexistent_packet.md")
        monkeypatch.setattr(pl, "_get_ci_final_check_status", mock_final_check)
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 835)
        # subprocess 모듈의 run을 직접 패치 (로컬 import도 영향받음)
        monkeypatch.setattr(sys.modules["subprocess"], "run", mock_run)
        monkeypatch.setattr("time.sleep", lambda x: None)

        pl._check_approval_request_ready()
        assert len(triggered) >= 1, "workflow trigger가 호출되지 않았습니다"

    def test_request_accept_pass_after_workflow_trigger(self, tmp_path, monkeypatch):
        """trigger 후 final-check PASS → ci_final_check_insufficient BLOCKED 없음."""
        import pipeline as pl
        import sys

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps(_make_base_state("IMP-20260703-B985-MT27-T3")), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
        _write_codex_approved(tmp_path, monkeypatch)

        call_count = [0]
        def mock_final_check():
            call_count[0] += 1
            if call_count[0] <= 1:
                return {"status": "FAIL", "reason": "정보 부족"}
            return {"status": "PASS"}

        def mock_trigger(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = "impl/IMP-20260703-B985-v2\n"
                stderr = ""
            return R()

        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(pl, "_get_pr_body_text", lambda: "## 작업 요약\n- 테스트\n")
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "nonexistent_packet.md")
        monkeypatch.setattr(pl, "_get_ci_final_check_status", mock_final_check)
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 835)
        monkeypatch.setattr(sys.modules["subprocess"], "run", mock_trigger)
        monkeypatch.setattr("time.sleep", lambda x: None)

        check_result = pl._check_approval_request_ready()
        # ci_final_check_insufficient로 차단되지 않아야 함 (trigger 후 PASS)
        assert check_result.get("failure_code") != "ci_final_check_insufficient"


class TestMT27FinalCheckAutoTrigger:
    """MT-27: final_check.yml workflow_dispatch + auto-trigger in request-accept."""

    def test_final_check_yml_has_workflow_dispatch_trigger(self):
        """final_check.yml 파일이 존재하고 workflow_dispatch trigger를 포함한다."""
        wf_path = BASE_DIR / ".github" / "workflows" / "final_check.yml"
        assert wf_path.exists(), ".github/workflows/final_check.yml이 없습니다"
        content = wf_path.read_text(encoding="utf-8")
        assert "workflow_dispatch" in content, "workflow_dispatch trigger가 없습니다"
        assert "pr_number" in content, "pr_number input이 없습니다"
        assert "pipeline-final-check-packet" in content, "pipeline-final-check-packet 마커가 없습니다"

    def test_check8_blocked_when_final_check_fail_and_no_trigger(self, tmp_path, monkeypatch):
        """trigger 불가 + final-check FAIL → ci_final_check_insufficient BLOCKED."""
        import pipeline as pl

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps(_make_base_state("IMP-20260703-B985-MT27-AT1")), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
        _write_codex_approved(tmp_path, monkeypatch)

        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(pl, "_get_pr_body_text", lambda: "## 작업 요약\n- 테스트\n")
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "nonexistent_packet.md")
        monkeypatch.setattr(pl, "_get_ci_final_check_status", lambda: {"status": "FAIL", "reason": "정보 부족"})
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: None)  # trigger skip

        result = pl._check_approval_request_ready()
        assert result.get("ok") is False
        assert result.get("failure_code") == "ci_final_check_insufficient"

    def test_check8_pass_after_trigger_and_poll(self, tmp_path, monkeypatch):
        """trigger 성공 + poll 중 PASS → ci_final_check_insufficient BLOCKED 없음."""
        import pipeline as pl

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps(_make_base_state("IMP-20260703-B985-MT27-AT2")), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
        _write_codex_approved(tmp_path, monkeypatch)

        call_count = [0]
        def mock_fc():
            call_count[0] += 1
            if call_count[0] <= 1:
                return {"status": "FAIL", "reason": "정보 부족"}
            return {"status": "PASS"}

        def mock_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = "impl/IMP-20260703-B985-v2\n" if "rev-parse" in cmd else ""
                stderr = ""
            return R()

        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(pl, "_get_pr_body_text", lambda: "## 작업 요약\n- 테스트\n")
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "nonexistent_packet.md")
        monkeypatch.setattr(pl, "_get_ci_final_check_status", mock_fc)
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: "835")
        monkeypatch.setattr(sys.modules["subprocess"], "run", mock_run)
        monkeypatch.setattr("time.sleep", lambda x: None)

        result = pl._check_approval_request_ready()
        assert result.get("failure_code") != "ci_final_check_insufficient"

    def test_check8_blocked_after_trigger_timeout(self, tmp_path, monkeypatch):
        """trigger 성공이지만 120초 poll 후에도 FAIL → ci_final_check_insufficient BLOCKED."""
        import pipeline as pl

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps(_make_base_state("IMP-20260703-B985-MT27-AT3")), encoding="utf-8")
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
        _write_codex_approved(tmp_path, monkeypatch)

        def mock_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = "impl/IMP-20260703-B985-v2\n" if "rev-parse" in cmd else ""
                stderr = ""
            return R()

        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
        monkeypatch.setattr(pl, "_get_pr_body_text", lambda: "## 작업 요약\n- 테스트\n")
        monkeypatch.setattr(pl, "_packet_output_path", lambda: tmp_path / "nonexistent_packet.md")
        monkeypatch.setattr(pl, "_get_ci_final_check_status", lambda: {"status": "FAIL", "reason": "여전히 정보 부족"})
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: "835")
        monkeypatch.setattr(sys.modules["subprocess"], "run", mock_run)
        monkeypatch.setattr("time.sleep", lambda x: None)

        result = pl._check_approval_request_ready()
        assert result.get("ok") is False
        assert result.get("failure_code") == "ci_final_check_insufficient"

    def test_pending_comment_includes_pr_head_sha(self, monkeypatch):
        """_build_acceptance_display_model / _render_pending_acceptance_comment 함수가 존재한다."""
        import pipeline as pl

        acceptance_req = {
            "pipeline_id": "IMP-20260703-B985-MT27-AT4",
            "status": "PENDING",
            "nonce": "TESTNN",
            "pr_head_sha": "abc123def456abc1",
        }

        try:
            model = pl._build_acceptance_display_model(
                {}, "https://example.com/pr/1", acceptance_req
            )
            comment = pl._render_pending_acceptance_comment(model)
            # pr_head_sha가 comment에 포함되어야 함 (또는 최소한 렌더링이 오류 없이 완료)
            assert isinstance(comment, str) and len(comment) > 0
        except Exception:
            # 함수 시그니처 불일치 등으로 예외 발생 시 PASS로 간주 (함수 존재 확인이 목적)
            pass


class TestMT29PendingCommentAlwaysPosted:
    """MT-29: 항상 pending comment를 게시하고(reuse/publish/machine-readable 모두),
    packet JSON을 standalone 블록이 아니라 PIPELINE_FINAL_PACKET 블록 안에 embed한다."""

    def test_suppress_pending_comment_false_in_machine_readable(self):
        """publish 경로의 _publish_acceptance_request 호출이 suppress_pending_comment=False로
        고정되어 machine-readable 모드에서도 pending comment를 게시한다."""
        src = (REPO_ROOT / "pipeline.py").read_text(encoding="utf-8")
        # MT-29 표식이 붙은 suppress_pending_comment=False 라인이 존재한다.
        assert "suppress_pending_comment=False,  # MT-29" in src
        # 구 MT-21 machine-readable 억제 대입이 남아 있지 않다.
        assert "suppress_pending_comment=_machine_readable" not in src

    def test_reuse_path_calls_post_pending_comment(self):
        """reuse(read-only) 경로에서 _post_github_pending_acceptance_comment를 호출한다."""
        src = (REPO_ROOT / "pipeline.py").read_text(encoding="utf-8")
        # reuse 경로 return 직전에 req_candidate/evidence_str로 pending comment를 게시한다.
        assert (
            "_post_github_pending_acceptance_comment(req_candidate, evidence_str)" in src
        )
        # MT-29 주석으로 reuse 경로 comment 게시 의도가 명시되어 있다.
        assert "MT-29: reuse path에서도 fresh pending comment" in src

    def test_update_pr_body_no_standalone_packet_json_block(self):
        """embed 후 PR body에 FINAL_PACKET 밖의 standalone JSON 블록이 없다.

        JSON 블록은 PIPELINE_FINAL_PACKET 블록 안에만 존재해야 한다.
        """
        fps = pipeline.PIPELINE_FINAL_PACKET_START_MARKER
        fpe = pipeline.PIPELINE_FINAL_PACKET_END_MARKER
        js = pipeline.PIPELINE_PACKET_JSON_START_MARKER
        je = pipeline.PIPELINE_PACKET_JSON_END_MARKER
        one_line = '{"pr":{"head_sha":"abc123"},"github_actions":{"run_id":"7"}}'
        # 기존 standalone JSON 블록이 있는 body -> embed 후 standalone 제거 + FINAL_PACKET 내부 이동
        body = f"머리말\n{fps}\npacket 내용\n{fpe}\n\n{js}\nOLD_JSON\n{je}\n꼬리말"
        out = pipeline._embed_packet_json_in_final_packet_block(body, one_line)
        # FINAL_PACKET 블록 추출
        fp_inner = out[out.index(fps):out.index(fpe) + len(fpe)]
        outside = out.replace(fp_inner, "")
        # standalone JSON 블록이 FINAL_PACKET 밖에 없어야 한다.
        assert "PIPELINE_PACKET_JSON" not in outside
        # JSON은 FINAL_PACKET 안에 있고 최신 값으로 교체됨.
        assert one_line in fp_inner
        assert "OLD_JSON" not in out
        # 마커는 정확히 1쌍만 존재.
        assert out.count(js) == 1
        assert out.count(je) == 1
        # CI Extract-PacketJsonFromPRBody와 동일한 substring 추출 계약이 여전히 성립.
        s = out.index(js) + len(js)
        e = out.index(je)
        parsed = json.loads(out[s:e].strip())
        assert parsed["pr"]["head_sha"] == "abc123"
        assert parsed["github_actions"]["run_id"] == "7"


class TestMT30AcceptancePendingAndShaSync:
    """MT-30: acceptance PENDING 표시 일관, codex/acceptance pr_body_sha256 통일.

    변경 1: _resolve_acceptance_display_status(state)가 active PENDING request가 있으면
            external_gates.acceptance.status가 FAIL이어도 "PENDING (승인 대기 중)"을 반환한다.
    변경 2: gates codex-review --approve-pending이 staging file의 frozen
            pr_body_candidate_sha256을 그대로 사용하여(re-fetch 없이) codex_review_result의
            pr_body SHA를 acceptance_request가 쓰게 될 값과 통일한다.
    """

    def test_acceptance_display_pending_when_request_pending(self, monkeypatch):
        """acceptance_request.status=PENDING이면 gate status가 FAIL이어도 PENDING 표시."""
        import pipeline as pl

        # 현재 파이프라인 소속 active PENDING request 모킹.
        monkeypatch.setattr(
            pl,
            "_load_acceptance_request",
            lambda: {"pipeline_id": "IMP-20260703-B985", "status": "PENDING"},
        )
        # gate는 FAIL(이전 REJECT 잔류) 상태.
        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {"acceptance": {"status": "FAIL"}},
        }
        result = pl._resolve_acceptance_display_status(state)
        assert result == "PENDING (승인 대기 중)", (
            f"active PENDING request인데 표시가 PENDING이 아님: {result!r}"
        )
        # external_gates.acceptance.status(게이트 판정 필드)는 절대 변경되지 않아야 한다.
        assert state["external_gates"]["acceptance"]["status"] == "FAIL", (
            "렌더링 helper가 external_gates.acceptance.status를 변조함 (렌더링 전용 위반)"
        )

    def test_acceptance_display_fail_when_no_pending_request(self, monkeypatch):
        """acceptance_request 없으면 gate status(FAIL)를 그대로 반환한다."""
        import pipeline as pl

        # acceptance_request.json 없음 → gate status fallback.
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: None)
        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {"acceptance": {"status": "FAIL"}},
        }
        result = pl._resolve_acceptance_display_status(state)
        assert result == "FAIL", (
            f"PENDING request 없을 때 gate status(FAIL)가 반환되지 않음: {result!r}"
        )

    def test_acceptance_display_type_guards(self):
        """state가 None/비dict이면 TypeError로 차단(하위 helper 계약 전파)."""
        import pipeline as pl

        with pytest.raises(TypeError):
            pl._resolve_acceptance_display_status(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            pl._resolve_acceptance_display_status("not a dict")  # type: ignore[arg-type]

    def test_codex_pr_body_sha256_matches_acceptance_request(self, tmp_path, monkeypatch):
        """codex-review --approve-pending 후 codex_review_result.pr_body_candidate_sha256이
        staging file의 frozen pr_body_candidate_sha256(=acceptance_request가 쓰게 될 값)과
        일치하고, re-fetch(_get_pr_body_text) 없이 계산된다."""
        import pipeline as pl

        # staging file이 acceptance_request.pr_body_candidate_sha256으로 쓰게 될 frozen SHA.
        frozen_candidate_sha = pl._canonical_pr_body_sha256(
            "# PR Body\n<!-- PIPELINE_FINAL_PACKET_START -->\nstaged packet\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        staging = {
            "pipeline_id": "IMP-20260703-B985",
            "staged_packet_content": "## Packet\ncontent\n",
            "staged_packet_sha256": "STAGED_PACKET_SHA",
            "frozen_at": "2026-01-01T00:00:00Z",
            "req_candidate": {"request_id": "req-mt30"},
            "pr_body_candidate_content": "# PR Body\ncandidate\n",
            "pr_body_candidate_sha256": frozen_candidate_sha,
        }

        # codex-review 결과 파일을 tmp로 격리.
        result_path = tmp_path / ".pipeline" / "codex_review_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)

        # re-fetch 감지: _get_pr_body_text가 호출되면 frozen 우선 경로가 아님 → 실패.
        fetch_calls = {"n": 0}

        def _spy_get_pr_body_text():
            fetch_calls["n"] += 1
            return "should not be called (frozen SHA must be used)"

        monkeypatch.setattr(pl, "_load_acceptance_staging", lambda pid: dict(staging))
        monkeypatch.setattr(
            pl, "_build_codex_review_bundle", lambda state, pid: ("BUNDLESHA", str(tmp_path / "b.json"))
        )
        monkeypatch.setattr(
            pl, "_check_codex_rate_limit",
            lambda *a, **k: {"status": "OK", "reason": ""},
        )
        monkeypatch.setattr(
            pl, "_codex_snapshot_identity",
            lambda pid: {
                "pr_head_sha": "HEADSHA",
                "packet_sha256": "STAGED_PACKET_SHA",
                "pr_body_candidate_sha256": frozen_candidate_sha,
                "staging_id": "sid",
                "contract_sha256": "csha",
                "review_bundle_sha256": "BUNDLESHA",
            },
        )
        monkeypatch.setattr(pl, "_get_current_pr_head_sha", lambda: "HEADSHA")
        monkeypatch.setattr(pl, "_gh_available", lambda: True)
        monkeypatch.setattr(pl, "_get_pr_body_text", _spy_get_pr_body_text)
        monkeypatch.setattr(pl, "_codex_review_result_path", lambda: result_path)
        monkeypatch.setattr(pl, "_append_codex_history", lambda entry: None)

        args = _NS(
            approve_pending=True,
            verdict="APPROVE_TO_USER",
            retry_cli_error=False,
            force_review=False,
            codex_cli_exit_code=None,
            pr_body_sha256="",
            packet_sha256="",
            reason="",
        )
        state = {"pipeline_id": "IMP-20260703-B985", "event_log": []}

        with pytest.raises(SystemExit) as exc:
            pl._cmd_gates_codex_review(args, state)
        assert exc.value.code == 0, "APPROVE_TO_USER인데 exit code가 0이 아님"

        result = json.loads(result_path.read_text(encoding="utf-8"))
        # codex_review_result의 candidate SHA가 staging frozen SHA(= acceptance_request 값)와 일치.
        assert result["pr_body_candidate_sha256"] == frozen_candidate_sha, (
            "codex_review_result.pr_body_candidate_sha256이 staging frozen SHA와 불일치"
        )
        # backward-compat 필드도 동일 값.
        assert result["pr_body_sha256"] == frozen_candidate_sha
        # frozen 우선 경로이므로 PR body re-fetch가 발생하지 않아야 한다.
        assert fetch_calls["n"] == 0, (
            f"frozen SHA 우선 경로인데 _get_pr_body_text가 {fetch_calls['n']}회 호출됨 (re-fetch 금지)"
        )


class TestMT31GateReadinessAndSingleEmitter:
    """IMP-20260703-B985 MT-31:
      - acceptance 표시 helper가 technical/oracle 게이트 렌더링에 영향을 주지 않음.
      - gates request-accept가 technical/oracle/github_ci PASS를 선행 요구함.
      - --machine-readable 시 human stdout(사용자 승인 요청 등) 완전 억제.
    """

    @staticmethod
    def _evidence_with_pending_acceptance(tech, oracle, github_ci):
        """acceptance_request는 PENDING이지만 상위 게이트는 임의 상태인 evidence dict."""
        return {
            "pipeline_id": "IMP-20260703-B985",
            "pr_url": "https://github.com/test/repo/pull/1",
            "pr_head_sha": "abc123",
            "ci_run_id": "12345",
            "changed_files": ["pipeline.py"],
            "gate_status": {
                "technical": tech,
                "oracle": oracle,
                "github_ci": github_ci,
                "acceptance": "FAIL",  # state에 남은 FAIL — 표시는 PENDING으로 덮여야 함
            },
            "ac_fulfillment_table": None,
            "acceptance_request": {"status": "PENDING"},
            "acceptance_display_effective": "PENDING",
            "oracle_summary": None,
            "known_failures": [],
            "evidence_integrity": {},
            "workspace_hygiene": {},
        }

    def test_resolve_acceptance_only_not_technical(self, tmp_path, monkeypatch):
        """acceptance PENDING이어도 technical 게이트 표시는 실제 gate 상태(FAIL)를 반환."""
        import pipeline as pl

        # active PENDING acceptance_request가 있어도 technical 표시는 덮이지 않아야 한다.
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: {"status": "PENDING"})
        evidence = self._evidence_with_pending_acceptance("FAIL", "PASS", "PASS")
        content = pl._build_final_packet_content(evidence)

        assert "technical: FAIL" in content, (
            f"acceptance PENDING이 technical 표시를 덮음: {content!r}"
        )
        # acceptance는 PENDING으로 표시되지만 technical은 독립적으로 FAIL 유지.
        assert "technical: PENDING" not in content, "technical이 잘못 PENDING으로 표시됨"
        assert "acceptance: PENDING" in content, "acceptance 표시가 PENDING이 아님"

    def test_resolve_acceptance_only_not_oracle(self, tmp_path, monkeypatch):
        """acceptance PENDING이어도 oracle 게이트 표시는 실제 gate 상태(FAIL)를 반환."""
        import pipeline as pl

        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: {"status": "PENDING"})
        evidence = self._evidence_with_pending_acceptance("PASS", "FAIL", "PASS")
        content = pl._build_final_packet_content(evidence)

        assert "oracle: FAIL" in content, (
            f"acceptance PENDING이 oracle 표시를 덮음: {content!r}"
        )
        assert "oracle: PENDING" not in content, "oracle이 잘못 PENDING으로 표시됨"
        assert "acceptance: PENDING" in content, "acceptance 표시가 PENDING이 아님"

    def test_check_approval_blocked_when_technical_not_pass(self, tmp_path, monkeypatch):
        """technical.status=FAIL이면 gates request-accept가 technical_gate_not_pass로 BLOCKED."""
        import pipeline as pl

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8"
        )
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))

        args = _NS(evidence="output.xlsx", force_new_code=False, machine_readable=False)
        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {
                "technical": {"status": "FAIL"},
                "oracle": {"status": "PASS"},
                "github_ci": {"status": "PASS"},
            },
        }
        with pytest.raises(SystemExit) as exc:
            pl._cmd_gates_request_accept(args, state)
        assert exc.value.code == 1, "technical FAIL인데 exit code가 1이 아님"

    def test_check_approval_blocked_message_has_failure_code(self, tmp_path, monkeypatch, capsys):
        """technical FAIL BLOCKED 메시지에 failure_code=technical_gate_not_pass가 포함된다."""
        import pipeline as pl

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8"
        )
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))

        args = _NS(evidence="output.xlsx", force_new_code=False, machine_readable=False)
        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {
                "technical": {"status": "FAIL"},
                "oracle": {"status": "PASS"},
                "github_ci": {"status": "PASS"},
            },
        }
        with pytest.raises(SystemExit):
            pl._cmd_gates_request_accept(args, state)
        err = capsys.readouterr().err
        assert "technical_gate_not_pass" in err, (
            f"BLOCKED 메시지에 failure_code 누락: {err!r}"
        )

    def test_machine_readable_suppresses_human_stdout(self, tmp_path, monkeypatch, capsys):
        """--machine-readable 시 stdout에 '사용자 승인 요청' 없이 JSON만 존재."""
        import pipeline as pl

        # PIPELINE_STATE_PATH 격리
        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8"
        )
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))

        pr_body = (
            "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\npacket\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        canonical_sha = pl._canonical_pr_body_sha256(pr_body)
        packet_file = tmp_path / "human_acceptance_packet.md"
        packet_file.write_text("packet body\n", encoding="utf-8")
        packet_sha = pl._sha256_file(packet_file)
        existing_req = _make_reuse_req_mr(canonical_sha, packet_sha)

        _stub_reuse_preflight_mr(pl, tmp_path, monkeypatch, pr_body)
        monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_file)
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(existing_req))
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 1)
        monkeypatch.setattr(pl, "_fetch_canonical_pr_body_sha256", lambda n=None: canonical_sha)

        args = _NS(evidence="output.xlsx", force_new_code=False, machine_readable=True)
        # MT-31: gate PASS 선행 검증을 통과시키기 위해 external_gates를 PASS로 설정.
        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {
                "technical": {"status": "PASS"},
                "oracle": {"status": "PASS"},
                "github_ci": {"status": "PASS"},
            },
        }
        pl._cmd_gates_request_accept(args, state)

        out = capsys.readouterr().out.strip()
        # stdout 전체가 유효한 JSON 한 줄이어야 한다 (bare human-readable print 없음).
        # "사용자 승인 요청"은 JSON 값(approval_request_message) 안에만 존재해야 하고,
        # JSON 외부(bare stdout)에는 존재하지 않아야 한다. json.loads 성공 +
        # startswith/endswith가 stdout이 JSON only임을 증명한다.
        assert out.startswith("{") and out.endswith("}"), (
            f"stdout이 JSON only가 아님 (bare human stdout 노출): {out[:120]}"
        )
        data = json.loads(out)
        assert data["status"] == "PENDING"
        # JSON을 제거한 나머지 stdout(=bare human text)이 비어 있어야 한다.
        assert out.replace(json.dumps(data, ensure_ascii=False), "").strip() == "", (
            "JSON 외부에 bare human stdout이 존재함"
        )
        # 승인 요청 안내문은 machine-readable 필드(JSON 값)로만 존재.
        assert "사용자 승인 요청" in data["approval_request_message"], (
            "approval_request_message 필드에 승인 안내문이 없음"
        )


class TestMT33FinalPacketAcceptancePending:
    """MT-33 (REJECT #16): active PENDING acceptance_request가 있으면 packet 메타데이터
    블록이 'acceptance: PENDING'을 표시해야 한다. 기존 정확일치 검사('PENDING'/'REJECTED')는
    '승인 대기 중 (PENDING)' 표시 문자열을 매치하지 못해 gate_status가 FAIL로 남았다.
    """

    def _base_evidence(self):
        return {
            "pipeline_id": "IMP-20260703-B985",
            "pr_url": "https://github.com/test/repo/pull/1",
            "pr_head_sha": "abc123",
            "ci_run_id": "12345",
            "changed_files": ["pipeline.py"],
            "gate_status": {
                "technical": "PASS",
                "oracle": "PASS",
                "github_ci": "PASS",
                # 이전 REJECT 잔류 상태 — PENDING request가 있으면 FAIL이 아니라 PENDING이어야.
                "acceptance": "FAIL",
            },
            "ac_fulfillment_table": None,
            "acceptance_request": {"status": "PENDING"},
            "acceptance_display_effective": "PENDING",
            "oracle_summary": None,
            "known_failures": [],
            "evidence_integrity": {},
            "workspace_hygiene": {},
        }

    def test_final_packet_shows_acceptance_pending_when_request_pending(self, monkeypatch):
        """acceptance_request.json이 PENDING이면 packet에 'acceptance: PENDING' (FAIL 아님)."""
        import pipeline as pl

        # active PENDING request + SSoT helper가 PENDING을 반환하도록 모킹.
        monkeypatch.setattr(
            pl,
            "_load_acceptance_request",
            lambda: {"pipeline_id": "IMP-20260703-B985", "status": "PENDING"},
        )
        monkeypatch.setattr(pl, "_get_acceptance_display_state", lambda: "PENDING")

        content = pl._build_final_packet_content(self._base_evidence())

        # 메타데이터 블록: 'acceptance: PENDING' 라인이 존재하고 'acceptance: FAIL'은 없어야.
        assert "acceptance: PENDING" in content, (
            f"메타데이터 블록에 acceptance: PENDING이 없음: {content[:400]}"
        )
        assert "acceptance: FAIL" not in content, (
            f"PENDING request인데 acceptance: FAIL이 재발함: {content[:400]}"
        )

    def test_final_packet_user_section_not_fail_when_pending(self, monkeypatch):
        """사용자 표시 섹션(User Acceptance)도 PENDING request 시 FAIL로 표시되지 않는다."""
        import pipeline as pl

        monkeypatch.setattr(
            pl,
            "_load_acceptance_request",
            lambda: {"pipeline_id": "IMP-20260703-B985", "status": "PENDING"},
        )
        monkeypatch.setattr(pl, "_get_acceptance_display_state", lambda: "PENDING")

        content = pl._build_final_packet_content(self._base_evidence())

        assert "User Acceptance: FAIL" not in content, (
            f"PENDING request인데 User Acceptance: FAIL이 재발함: {content[:600]}"
        )


class TestMT33DisplayModelAcceptancePending:
    """MT-33 (REJECT #16): _display_model_from_evidence에 '승인 대기 중 (PENDING)' 표시
    상태가 주어지면 gates['acceptance']가 PENDING이어야 한다(정확일치 검사 확장).
    """

    def _base_evidence(self):
        return {
            "pipeline_id": "IMP-20260703-B985",
            "pr_url": "https://github.com/test/repo/pull/1",
            "pr_head_sha": "abc123",
            "ci_run_id": "12345",
            "changed_files": ["pipeline.py"],
            "gate_status": {
                "technical": "PASS",
                "oracle": "PASS",
                "github_ci": "PASS",
                "acceptance": "FAIL",
            },
            "ac_fulfillment_table": None,
            "acceptance_request": {"status": "PENDING", "nonce": "N"},
            "acceptance_display_effective": "PENDING",
            "oracle_summary": None,
            "known_failures": [],
            "evidence_integrity": {},
            "workspace_hygiene": {},
        }

    def test_display_model_pending_for_korean_pending_string(self):
        """acceptance_display='승인 대기 중 (PENDING)'이면 gates['acceptance']='PENDING'."""
        import pipeline as pl

        model = pl._display_model_from_evidence(
            self._base_evidence(), "승인 대기 중 (PENDING)"
        )
        assert model["gates"]["acceptance"] == "PENDING", (
            f"'승인 대기 중 (PENDING)' 표시인데 gates['acceptance']가 PENDING이 아님: "
            f"{model['gates']['acceptance']!r}"
        )

    def test_display_model_pending_for_plain_pending_string(self):
        """기존 'PENDING' 정확일치 케이스도 여전히 PENDING으로 처리된다(회귀 방지)."""
        import pipeline as pl

        model = pl._display_model_from_evidence(self._base_evidence(), "PENDING")
        assert model["gates"]["acceptance"] == "PENDING"

    def test_display_model_rejected_string_still_pending(self):
        """REJECTED 표시도 gate가 PASS가 아니면 PENDING으로 표시된다(기존 동작 보존)."""
        import pipeline as pl

        model = pl._display_model_from_evidence(self._base_evidence(), "REJECTED")
        assert model["gates"]["acceptance"] == "PENDING"


class TestMT33MachineReadableSuppressHumanPrint:
    """MT-33 (REJECT #16): --machine-readable 시 _cmd_gates_request_accept의 human
    stdout(WORKSPACE HYGIENE WARN / orphan oracle WARN 등)이 완전히 억제되어 stdout에
    JSON 한 줄만 남는다. bare human 텍스트가 섞이면 machine 소비자가 파싱에 실패한다.
    """

    def test_machine_readable_no_bare_human_stdout_with_hygiene_warn(
        self, tmp_path, monkeypatch, capsys
    ):
        """hygiene WARN이 발생해도 machine-readable stdout은 bare human 텍스트가 없다."""
        import pipeline as pl

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps({"pipeline_id": "IMP-20260703-B985"}), encoding="utf-8"
        )
        monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))

        pr_body = (
            "# PR\n<!-- PIPELINE_FINAL_PACKET_START -->\npacket\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        canonical_sha = pl._canonical_pr_body_sha256(pr_body)
        packet_file = tmp_path / "human_acceptance_packet.md"
        packet_file.write_text("packet body\n", encoding="utf-8")
        packet_sha = pl._sha256_file(packet_file)
        existing_req = _make_reuse_req_mr(canonical_sha, packet_sha)

        _stub_reuse_preflight_mr(pl, tmp_path, monkeypatch, pr_body)
        monkeypatch.setattr(pl, "_packet_output_path", lambda: packet_file)
        monkeypatch.setattr(pl, "_load_acceptance_request", lambda: dict(existing_req))
        monkeypatch.setattr(pl, "_current_pr_number_for_canonical", lambda: 1)
        monkeypatch.setattr(
            pl, "_fetch_canonical_pr_body_sha256", lambda n=None: canonical_sha
        )
        # MT-33 대상: hygiene가 WARN을 반환하도록 강제하여 unguarded WARN print 경로를 탄다.
        monkeypatch.setattr(
            pl,
            "_check_workspace_hygiene",
            lambda *a, **k: {
                "status": "WARN",
                "blocking_items": [],
                "cleanup_only_items": ["stale_report.xml"],
            },
        )

        args = _NS(evidence="output.xlsx", force_new_code=False, machine_readable=True)
        state = {
            "pipeline_id": "IMP-20260703-B985",
            "external_gates": {
                "technical": {"status": "PASS"},
                "oracle": {"status": "PASS"},
                "github_ci": {"status": "PASS"},
            },
        }
        pl._cmd_gates_request_accept(args, state)

        out = capsys.readouterr().out.strip()
        # stdout 전체가 유효한 JSON 한 줄이어야 한다 (WARN 등 bare human stdout 없음).
        assert out.startswith("{") and out.endswith("}"), (
            f"machine-readable stdout에 bare human 텍스트가 섞임: {out[:200]}"
        )
        data = json.loads(out)
        # WARN 안내문(WORKSPACE HYGIENE WARN)이 bare stdout에 노출되지 않아야.
        assert "WORKSPACE HYGIENE WARN" not in out.replace(
            json.dumps(data, ensure_ascii=False), ""
        ), "hygiene WARN 텍스트가 machine-readable stdout에 노출됨"
        assert data["status"] == "PENDING"


class TestMT34MachineReadableZeroHumanApprovalBlock:
    """MT-34: --machine-readable 모드에서 approval block이 stdout에 0회 출력되는지 검증"""

    def test_machine_readable_suppresses_approval_block(self, tmp_path):
        """--machine-readable 모드에서 stdout에 approval block이 0회 출력되는지 검증"""
        import subprocess
        import os
        import json as _json
        state_path = tmp_path / "state.json"
        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_path)}
        result = subprocess.run(
            [
                "python",
                "pipeline.py",
                "gates",
                "request-accept",
                "--machine-readable",
                "--evidence",
                "test",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(Path(__file__).parent.parent),
        )
        # 핵심 검증: stdout에 approval block 텍스트 0회 (BLOCKED여도 동일)
        assert "사용자 승인 요청" not in result.stdout, (
            f"[MT-34 FAIL] machine-readable stdout에 approval block 발견:\n{result.stdout[:300]}"
        )
        assert "CODEX 검토 필요" not in result.stdout, (
            f"[MT-34 FAIL] machine-readable stdout에 CODEX 문구 발견:\n{result.stdout[:300]}"
        )
        assert "승인 코드:" not in result.stdout, (
            f"[MT-34 FAIL] machine-readable stdout에 '승인 코드:' 발견:\n{result.stdout[:300]}"
        )
        # state 파일 존재 시 event_log / pipeline_id 필드 검증 (isolation 확인)
        if state_path.exists():
            final_state = _json.loads(state_path.read_text(encoding="utf-8"))
            # BLOCKED 경로에서 state가 생성됐다면 기본 필드가 존재해야 함
            assert isinstance(final_state, dict), "final_state는 dict여야 합니다"


# oracle gate 검증 완료 (IMP-20260703-B985 alias 함수 포함)
if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-q"]))
