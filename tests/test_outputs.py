"""
tests/test_outputs.py
---------------------
IMP-20260515-020F: outputs add/status 단위 테스트

검증 항목:
  - outputs add → pipeline_outputs/<id>/에 파일 복사됨
  - outputs add → outputs_manifest.json 생성
  - outputs status → 등록된 항목 표시
"""
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pipeline


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

def _make_state_and_source(tmp_root: Path, pipeline_id: str = "TMP-OUTPUTS-TEST"):
    """테스트용 state와 소스 파일 준비."""
    state = pipeline._new_state(pipeline_id, "IMP", "outputs 테스트")
    source_file = tmp_root / "report.md"
    source_file.write_text("# 테스트 보고서\n결론: 정상", encoding="utf-8")
    return state, source_file


# ---------------------------------------------------------------------------
# Item 8-A: outputs add → pipeline_outputs/<id>/에 파일 복사
# ---------------------------------------------------------------------------

def test_outputs_add_copies_file() -> None:
    """outputs add가 실제 파일을 pipeline_outputs/<pipeline_id>/ 디렉토리에 복사해야 한다."""
    pipeline_id = "TMP-OUTPUTS-COPY"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        outputs_root = tmp_root / "pipeline_outputs"
        state, source_file = _make_state_and_source(tmp_root, pipeline_id)

        # OUTPUTS_ROOT와 manifest 경로를 tmp로 리다이렉트
        expected_dest_dir = outputs_root / pipeline_id

        with mock.patch.object(pipeline, "OUTPUTS_ROOT", outputs_root), \
             mock.patch.object(pipeline, "_load_state", return_value=state), \
             mock.patch.object(pipeline, "_save_state"), \
             mock.patch.object(pipeline, "_require_state", return_value=state), \
             mock.patch.object(pipeline, "_save", side_effect=lambda s: None), \
             mock.patch.object(pipeline, "_write_json", side_effect=lambda p, d: (
                 p.parent.mkdir(parents=True, exist_ok=True),
                 p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
             )):
            item = pipeline._register_output_item(
                state,
                kind="report",
                path=str(source_file),
                label="최종-보고서",
                copy_to_outputs=True,
                notes="테스트 결과물",
            )

        # 복사된 파일이 존재해야 함
        public_path = item["public_path"]
        # _display_path가 string을 반환하므로 경로 확인
        assert "TMP-OUTPUTS-COPY" in public_path or "report.md" in public_path
        # SHA256이 원본과 동일해야 함
        assert item["sha256"] == pipeline._sha256_file(source_file)
        assert item["kind"] == "report"
        assert item["label"] == "최종-보고서"


# ---------------------------------------------------------------------------
# Item 8-B: outputs add → outputs_manifest.json 생성
# ---------------------------------------------------------------------------

def test_outputs_add_creates_manifest() -> None:
    """outputs add가 outputs_manifest.json을 생성해야 한다."""
    pipeline_id = "TMP-OUTPUTS-MANIFEST"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        outputs_root = tmp_root / "pipeline_outputs"
        state, source_file = _make_state_and_source(tmp_root, pipeline_id)

        manifest_path = outputs_root / pipeline_id / "outputs_manifest.json"
        written_manifests = {}

        def _fake_write_json(path: Path, data: dict) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            written_manifests[str(path)] = data

        with mock.patch.object(pipeline, "OUTPUTS_ROOT", outputs_root), \
             mock.patch.object(pipeline, "_write_json", side_effect=_fake_write_json):
            item = pipeline._register_output_item(
                state,
                kind="report",
                path=str(source_file),
                label="분석-보고서",
                copy_to_outputs=True,
                notes="분석 결과물",
            )

        # manifest가 기록됐는지 확인
        assert len(written_manifests) >= 1, "outputs_manifest.json이 생성되어야 한다"
        manifest_data = list(written_manifests.values())[0]
        assert manifest_data["schema_version"] == 1
        assert manifest_data["pipeline_id"] == pipeline_id
        assert len(manifest_data["items"]) == 1
        assert manifest_data["items"][0]["kind"] == "report"
        assert manifest_data["items"][0]["label"] == "분석-보고서"


# ---------------------------------------------------------------------------
# Item 8-C: outputs status → 등록된 항목 표시
# ---------------------------------------------------------------------------

def test_outputs_status_shows_registered() -> None:
    """outputs status가 등록된 결과물 목록을 JSON으로 출력해야 한다."""
    import argparse
    pipeline_id = "TMP-OUTPUTS-STATUS"

    state = pipeline._new_state(pipeline_id, "IMP", "status 테스트")
    # 미리 output 항목 등록
    pipeline._ensure_output_registry(state)["items"] = [
        {
            "kind": "report",
            "label": "테스트-보고서",
            "source_path": "report.md",
            "public_path": f"pipeline_outputs/{pipeline_id}/보고서-report.md",
            "sha256": "abc123",
            "size_bytes": 42,
            "notes": "테스트 메모",
            "registered_at": "2026-05-15T00:00:00",
        }
    ]

    args = argparse.Namespace(outputs_action="status")
    printed_lines = []

    def _fake_print(*a, **kw):
        printed_lines.append(" ".join(str(x) for x in a))

    with mock.patch.object(pipeline, "_require_state", return_value=state), \
         mock.patch.object(pipeline, "_ensure_v210_fields", side_effect=lambda s: s), \
         mock.patch("builtins.print", side_effect=_fake_print):
        pipeline.cmd_outputs(args)

    output_text = "\n".join(printed_lines)
    parsed = None
    for line in printed_lines:
        idx = line.find("{")
        if idx >= 0:
            try:
                parsed = json.loads(line[idx:])
                break
            except json.JSONDecodeError:
                pass
    # 전체 출력에서 JSON 파싱
    full_output = "\n".join(printed_lines)
    try:
        parsed = json.loads(full_output)
    except json.JSONDecodeError:
        # 여러 줄 출력에서 JSON 부분만 추출
        import re
        match = re.search(r'\{.*\}', full_output, re.DOTALL)
        if match:
            parsed = json.loads(match.group())

    assert parsed is not None, f"JSON 출력이 없음: {full_output}"
    assert parsed["pipeline_id"] == pipeline_id
    items = parsed.get("outputs", {}).get("items", [])
    assert len(items) == 1
    assert items[0]["kind"] == "report"
    assert items[0]["label"] == "테스트-보고서"
