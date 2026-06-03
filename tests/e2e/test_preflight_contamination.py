"""
IMP-20260602-1ABE 회귀 테스트: preflight-pr-impl이 workspace 오염 파일을 잡는지 검증

이 테스트는 사용자 REJECT(PR #407 오염)로 발견된 결함을 회귀 검증합니다.
WORKSPACE_INTERNAL_PATTERNS과 WORKSPACE_INTERNAL_DIR_PREFIXES가 아래 파일 유형을 차단해야 합니다.
"""

import os
import sys
import pytest

# WORKSPACE_INTERNAL_PATTERNS를 직접 임포트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
try:
    from pipeline import (
        _is_internal_artifact,
        WORKSPACE_INTERNAL_PATTERNS,
        WORKSPACE_INTERNAL_DIR_PREFIXES,
        WORKSPACE_INTERNAL_EXTENSIONS,
    )
    HAS_PATTERNS = True
except ImportError:
    HAS_PATTERNS = False


@pytest.mark.skipif(not HAS_PATTERNS, reason="pipeline.py가 없거나 WORKSPACE_INTERNAL_PATTERNS 없음")
class TestWorkspaceContaminationPatterns:
    """WORKSPACE_INTERNAL_PATTERNS이 오염 파일 유형을 포함하는지 검증 (IMP-20260602-1ABE)"""

    def test_codex_agents_blocked(self):
        """Codex 설정 파일 차단 (.codex/ 디렉터리)"""
        assert _is_internal_artifact(".codex/agents/dev-agent.toml"), (
            ".codex/agents/ 경로는 WORKSPACE_INTERNAL_DIR_PREFIXES에 포함되어야 합니다"
        )

    def test_codex_directory_prefix_exists(self):
        """.codex/ 디렉터리 접두사가 WORKSPACE_INTERNAL_DIR_PREFIXES에 존재"""
        assert any(p.startswith(".codex") for p in WORKSPACE_INTERNAL_DIR_PREFIXES), (
            ".codex/ 접두사가 WORKSPACE_INTERNAL_DIR_PREFIXES에 포함되어야 합니다"
        )

    def test_po_automation_blocked(self):
        """Power Automate 디렉터리 차단"""
        assert _is_internal_artifact("po_automation/file_email_processor.py"), (
            "po_automation/ 경로는 WORKSPACE_INTERNAL_DIR_PREFIXES에 포함되어야 합니다"
        )

    def test_spec_files_blocked(self):
        """PyInstaller spec 파일 차단"""
        assert _is_internal_artifact("EmailMonitorFile.spec"), (
            "*.spec 파일은 WORKSPACE_INTERNAL_EXTENSIONS에 포함되어야 합니다"
        )

    def test_spec_extension_in_constants(self):
        """.spec 확장자가 WORKSPACE_INTERNAL_EXTENSIONS 상수에 존재"""
        assert ".spec" in WORKSPACE_INTERNAL_EXTENSIONS, (
            ".spec 확장자가 WORKSPACE_INTERNAL_EXTENSIONS에 포함되어야 합니다"
        )

    def test_pipeline_backup_tmp_blocked(self):
        """파이프라인 백업 임시 디렉터리 차단"""
        assert _is_internal_artifact("_pipeline_backup_tmp/agent_receipt.json"), (
            "_pipeline_backup_tmp/ 경로는 WORKSPACE_INTERNAL_DIR_PREFIXES에 포함되어야 합니다"
        )

    def test_pipeline_outputs_blocked(self):
        """pipeline outputs 디렉터리 차단"""
        assert _is_internal_artifact("pipeline_outputs/IMP-20260602-1ABE/result.txt"), (
            "pipeline_outputs/ 경로는 WORKSPACE_INTERNAL_DIR_PREFIXES에 포함되어야 합니다"
        )

    def test_scheduled_tasks_lock_blocked(self):
        """scheduled_tasks.lock 파일명 차단"""
        assert _is_internal_artifact("scheduled_tasks.lock"), (
            "scheduled_tasks.lock 파일은 WORKSPACE_INTERNAL_PATTERNS에 포함되어야 합니다"
        )

    def test_claude_patch_blocked(self):
        """claude_patch. 접두사 파일 차단"""
        assert _is_internal_artifact("claude_patch.patch"), (
            "claude_patch.* 파일은 WORKSPACE_INTERNAL_PATTERNS에 포함되어야 합니다"
        )

    def test_restore_scripts_blocked(self):
        """restore_ 접두사 파일 차단"""
        assert _is_internal_artifact("restore_state.py"), (
            "restore_*.py 임시 복구 스크립트는 WORKSPACE_INTERNAL_PATTERNS에 포함되어야 합니다"
        )

    def test_allowed_files_not_blocked(self):
        """허용 파일은 차단하지 않아야 함"""
        allowed = [
            "pipeline.py",
            "CLAUDE.md",
            "RELEASE_NOTES.md",
            ".claude/agents/pm-agent.md",
            ".claude/agents/dev-agent.md",
            ".claude/agents/qa-agent.md",
            ".claude/agents/test-harness-agent.md",
            "tests/e2e/test_ac_tracking_1abe.py",
            "tests/e2e/test_preflight_contamination.py",
            "tests/oracles/IMP-20260602-1ABE/case_normal_01/input.json",
            "tests/oracles/IMP-20260602-1ABE/oracle_manifest.json",
        ]
        for path in allowed:
            assert not _is_internal_artifact(path), (
                f"{path!r} 는 허용 파일이므로 _is_internal_artifact()가 False를 반환해야 합니다"
            )
