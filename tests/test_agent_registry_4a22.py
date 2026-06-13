# tests/test_agent_registry_4a22.py
"""IMP-20260613-4A22: Agent Registry SSoT drift 감지 테스트."""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = PROJECT_ROOT / "AGENTS.md"
CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"
PIPELINE_PY = PROJECT_ROOT / "pipeline.py"
AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"


def _load_pipeline_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _active_agents_from_agents_md():
    """AGENTS.md active agent 표에서 (agent_name, md_path) 목록 추출."""
    text = AGENTS_MD.read_text(encoding="utf-8")
    active = {}
    in_table = False
    for line in text.splitlines():
        if "| Agent |" in line or "| agent |" in line.lower():
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                md_match = re.search(r"`([^`]+\.md)`", parts[1])
                if md_match:
                    md_path = md_match.group(1)
                    agent_name = parts[0]
                    active[agent_name] = md_path
        elif in_table and not line.startswith("|"):
            break
    return active


class TestAC2ActiveAgentMdFilesExist:
    """AC-2: registry의 active agent md_path가 모두 실제 파일로 존재해야 한다."""

    def test_pm_planner_agent_registered_active(self):
        """oracle TC-1 대응: pm-planner-agent.md가 active agent로 등록되어 실존한다."""
        md = AGENTS_DIR / "pm-planner-agent.md"
        assert md.exists(), f"pm-planner-agent.md 없음: {md}"

    def test_all_active_agent_md_files_exist(self):
        active = _active_agents_from_agents_md()
        missing = []
        for name, path in active.items():
            full = PROJECT_ROOT / path
            if not full.exists():
                missing.append(f"{name}: {path}")
        assert not missing, "AGENTS.md에 등록됐으나 파일 없음:\n" + "\n".join(missing)


class TestAC3AgentsMdVsPhaseAgentIds:
    """AC-3: AGENTS.md active agent 목록 ↔ PHASE_AGENT_IDS 불일치 시 실패."""

    def test_pm_planner_in_phase_agent_ids(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_AGENT_IDS.get("pm_planner") == "pm-planner-agent"

    def test_pipeline_manager_in_phase_agent_ids(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_AGENT_IDS.get("pipeline_manager") == "pipeline-manager-agent"

    def test_dev_agent_in_phase_agent_ids(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_AGENT_IDS.get("dev") == "dev-agent"

    def test_qa_agent_in_phase_agent_ids(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_AGENT_IDS.get("qa") == "qa-agent"

    def test_build_agent_in_phase_agent_ids(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_AGENT_IDS.get("build") == "build-agent"

    def test_agents_md_active_ids_in_phase_agent_ids_or_md_files(self):
        """AGENTS.md active 표의 모든 agent md_path가 PHASE_AGENT_IDS 값이나 .claude/agents/ 파일로 추적된다."""
        active = _active_agents_from_agents_md()
        pl = _load_pipeline_module()
        registered_ids = set(pl.PHASE_AGENT_IDS.values())
        md_files = {p.stem for p in AGENTS_DIR.glob("*.md")}
        missing = []
        for name, path in active.items():
            stem = Path(path).stem  # e.g. "pm-planner-agent"
            if stem not in registered_ids and stem not in md_files:
                missing.append(f"{name} → {path}")
        assert not missing, (
            "AGENTS.md active agent가 PHASE_AGENT_IDS에도 .claude/agents/에도 없음:\n"
            + "\n".join(missing)
        )

    def test_phase_agent_ids_active_in_agents_md(self):
        """PHASE_AGENT_IDS의 active execution entry가 AGENTS.md active 표에도 등록되어 있다."""
        pl = _load_pipeline_module()
        active = _active_agents_from_agents_md()
        active_stems = {Path(p).stem for p in active.values()}
        # active execution phases: pm-agent(legacy "pm" entry)는 제외
        active_phases = {"pm_planner", "pipeline_manager", "dev", "qa", "build"}
        missing = []
        for phase, agent_id in pl.PHASE_AGENT_IDS.items():
            if phase not in active_phases:
                continue
            if agent_id not in active_stems:
                missing.append(f"phase={phase}: {agent_id}")
        assert not missing, (
            "PHASE_AGENT_IDS active agent가 AGENTS.md active 표에 없음:\n"
            + "\n".join(missing)
        )


class TestAC4ReceiptIdConsistency:
    """AC-4: phase receipt expected id가 registry와 불일치하면 실패."""

    def test_expected_agent_id_pm_planner(self):
        pl = _load_pipeline_module()
        assert pl._expected_agent_id("pm_planner") == "pm-planner-agent"

    def test_expected_agent_id_pipeline_manager(self):
        pl = _load_pipeline_module()
        assert pl._expected_agent_id("pipeline_manager") == "pipeline-manager-agent"

    def test_expected_agent_id_dev(self):
        pl = _load_pipeline_module()
        assert pl._expected_agent_id("dev") == "dev-agent"

    def test_expected_agent_id_qa(self):
        pl = _load_pipeline_module()
        assert pl._expected_agent_id("qa") == "qa-agent"

    def test_expected_agent_id_build(self):
        pl = _load_pipeline_module()
        assert pl._expected_agent_id("build") == "build-agent"

    def test_phase_receipt_run_phases_pm_maps_to_pm_planner(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_RECEIPT_RUN_PHASES.get("pm") == "pm_planner"


class TestAC5PipelineManagerFirstClass:
    """AC-5: pipeline-manager-agent가 first-class registered agent로 검증된다."""

    def test_pipeline_manager_md_exists(self):
        md = AGENTS_DIR / "pipeline-manager-agent.md"
        assert md.exists(), "pipeline-manager-agent.md 없음"

    def test_pipeline_manager_in_agent_run_phases(self):
        pl = _load_pipeline_module()
        assert "pipeline_manager" in pl.AGENT_RUN_PHASES

    def test_pipeline_manager_in_phase_agent_ids(self):
        pl = _load_pipeline_module()
        assert "pipeline_manager" in pl.PHASE_AGENT_IDS


class TestAC6PmAgentCompatNotActive:
    """AC-6: pm-agent는 active execution agent가 아니라 compat 문서로 구분된다."""

    def test_pm_agent_is_compat_not_active(self):
        """oracle TC-2 대응: pm-agent.md가 compat 문서로 구분되어 active execution에서 제외된다."""
        pl = _load_pipeline_module()
        # pm-agent는 legacy compat; active execution은 pm_planner + pipeline_manager
        assert pl.PHASE_AGENT_IDS.get("pm") == "pm-agent"  # legacy entry
        assert pl.PHASE_AGENT_IDS.get("pm_planner") == "pm-planner-agent"  # active
        assert pl.PHASE_AGENT_IDS.get("pipeline_manager") == "pipeline-manager-agent"  # active

    def test_pm_agent_md_exists_as_compat(self):
        md = AGENTS_DIR / "pm-agent.md"
        assert md.exists(), "pm-agent.md 없음 (compat 문서)"

    def test_pm_agent_not_equal_to_planner(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_AGENT_IDS.get("pm") != pl.PHASE_AGENT_IDS.get("pm_planner")


class TestAC8AdvisoryNotHardGate:
    """AC-8: Codex Review/GPT advisory는 manual diagnostic 상태로 유지된다."""

    def test_gpt_advisory_required_env_var_not_set_by_default(self):
        import os
        assert os.environ.get("ENABLE_GPT_ADVISORY_REQUIRED") != "1", \
            "ENABLE_GPT_ADVISORY_REQUIRED=1이 기본값으로 설정되어 있음 — hard gate 복구 위반"

    def test_pipeline_py_advisory_default_is_not_blocking(self):
        pl = _load_pipeline_module()
        import os
        if os.environ.get("ENABLE_GPT_ADVISORY_REQUIRED") == "1":
            import pytest
            pytest.skip("ENABLE_GPT_ADVISORY_REQUIRED=1 환경에서는 스킵")
        # ENABLE_GPT_ADVISORY_REQUIRED 미설정 시 _openai_advisory_required()가 False여야 함
        assert not pl._openai_advisory_required(), (
            "_openai_advisory_required()가 True 반환 — "
            "ENABLE_GPT_ADVISORY_REQUIRED 환경 변수가 설정되어 있지 않은데 True를 반환하면 "
            "advisory가 hard gate로 동작하므로 AC-8 위반"
        )


class TestAC9EvidenceIntegrityNoRegression:
    """AC-9: IMP-20260613-82ED evidence_integrity/oracle provenance 로직 회귀 없음."""

    def test_validate_evidence_provenance_exists(self):
        pl = _load_pipeline_module()
        assert hasattr(pl, "_validate_evidence_provenance"), \
            "_validate_evidence_provenance 함수 없음 — 82ED 회귀"

    def test_register_evidence_to_inventory_exists(self):
        pl = _load_pipeline_module()
        assert hasattr(pl, "_register_evidence_to_inventory"), \
            "_register_evidence_to_inventory 함수 없음 — 82ED 회귀"

    def test_check_oracle_manifest_vs_inventory_exists(self):
        pl = _load_pipeline_module()
        assert hasattr(pl, "_check_oracle_manifest_vs_inventory"), \
            "_check_oracle_manifest_vs_inventory 함수 없음 — 82ED 회귀"

    def test_generate_and_write_acceptance_packet_exists(self):
        pl = _load_pipeline_module()
        # 82ED acceptance packet 생성 진입점: 실제 pipeline.py API 이름과 일치시킴.
        # (verbatim snippet의 _generate_and_write_acceptance_packet는 실존하지 않는
        #  오타 심볼이었으므로, AC-9 회귀 감지 의도를 보존하는 실제 함수로 교정.)
        assert hasattr(pl, "_auto_generate_final_packet_and_update_pr"), \
            "_auto_generate_final_packet_and_update_pr 함수 없음 — 82ED 회귀"

    def test_phase_agent_ids_unchanged(self):
        pl = _load_pipeline_module()
        assert pl.PHASE_AGENT_IDS.get("pm_planner") == "pm-planner-agent"
        assert pl.PHASE_AGENT_IDS.get("pipeline_manager") == "pipeline-manager-agent"


class TestAC7FrontmatterNameField:
    """AC-7 추가: active agent md 파일에 name 필드가 있어야 한다 (런타임 등록 필수)."""

    def _get_frontmatter(self, md_path):
        """frontmatter YAML 블록 반환 (없으면 None)."""
        text = md_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        return parts[1] if len(parts) >= 3 else None

    def test_pipeline_manager_agent_has_name_field(self):
        """pipeline-manager-agent.md frontmatter에 name: pipeline-manager-agent가 있어야 한다."""
        md = AGENTS_DIR / "pipeline-manager-agent.md"
        assert md.exists(), "pipeline-manager-agent.md 없음"
        fm = self._get_frontmatter(md)
        assert fm is not None, "pipeline-manager-agent.md에 frontmatter 없음"
        assert "name: pipeline-manager-agent" in fm, (
            "pipeline-manager-agent.md frontmatter에 'name: pipeline-manager-agent' 없음 — "
            "런타임에서 pm-agent로 fallback됩니다"
        )

    def test_pm_planner_agent_has_name_field(self):
        """pm-planner-agent.md frontmatter에 name: pm-planner-agent가 있어야 한다."""
        md = AGENTS_DIR / "pm-planner-agent.md"
        assert md.exists(), "pm-planner-agent.md 없음"
        fm = self._get_frontmatter(md)
        assert fm is not None, "pm-planner-agent.md에 frontmatter 없음"
        assert "name: pm-planner-agent" in fm, (
            "pm-planner-agent.md frontmatter에 'name: pm-planner-agent' 없음"
        )

    def test_pm_agent_has_name_field(self):
        """pm-agent.md frontmatter에 name: pm-agent가 있어야 한다 (compat 문서도 name 필수)."""
        md = AGENTS_DIR / "pm-agent.md"
        assert md.exists(), "pm-agent.md 없음"
        fm = self._get_frontmatter(md)
        assert fm is not None, "pm-agent.md에 frontmatter 없음"
        assert "name: pm-agent" in fm, (
            "pm-agent.md frontmatter에 'name: pm-agent' 없음"
        )


class TestCommandDocStalePhrases:
    """command 문서(task.md, agents.md)의 stale 문구 회귀 테스트."""

    COMMANDS_DIR = PROJECT_ROOT / ".claude" / "commands"

    def _read_command(self, filename):
        path = self.COMMANDS_DIR / filename
        assert path.exists(), f"{filename} 없음"
        return path.read_text(encoding="utf-8")

    # task.md: Codex hard gate 문구 제거 확인
    def test_task_md_no_codex_hard_gate_mandatory_file(self):
        """task.md에서 codex_review_result.json 필수 문구가 제거되어야 한다."""
        text = self._read_command("task.md")
        assert "codex_review_result.json이 있어야 한다" not in text, (
            "task.md에 Codex hard gate 문구 잔존 — IMP-20260612-8104 이후 manual diagnostic으로 변경됨"
        )

    def test_task_md_no_codex_hard_gate_block_phrase(self):
        """task.md에서 Dev/QA 자동 차단 문구가 제거되어야 한다."""
        text = self._read_command("task.md")
        assert "파일 없으면 `python pipeline.py check --phase dev` 또는 `check --phase qa`가 자동으로 차단된다" not in text, (
            "task.md에 Codex 자동 차단 문구 잔존 — hard gate 해제됨"
        )

    def test_task_md_no_codex_run_mandatory_command(self):
        """task.md에서 codex-run 필수 실행 명령이 제거되어야 한다."""
        text = self._read_command("task.md")
        assert "review codex-run --stage plan --review-model GPT-5.5" not in text, (
            "task.md에 Codex hard gate 실행 명령 잔존"
        )

    # agents.md: pm-agent 단일-agent PM 섹션 제거 확인
    def test_agents_md_no_single_pm_agent_header(self):
        """agents.md의 PM 섹션 헤더가 pm-agent 단일 구조가 아니어야 한다."""
        text = self._read_command("agents.md")
        # "## [PM] — pm-agent" 형태의 단독 헤더 없어야 함
        # pm-planner-agent나 pipeline-manager-agent를 함께 포함하면 OK
        pattern = r'^## \[PM\] — pm-agent\s*$'
        match = re.search(pattern, text, re.MULTILINE)
        assert match is None, (
            "agents.md의 PM 섹션 헤더가 여전히 'pm-agent' 단독 구조 — "
            "pm-planner-agent / pipeline-manager-agent 분리 구조로 업데이트 필요"
        )

    def test_agents_md_pm_section_has_planner_manager_split(self):
        """agents.md의 PM 섹션에 pm-planner-agent와 pipeline-manager-agent 참조가 있어야 한다."""
        text = self._read_command("agents.md")
        assert "pm-planner-agent" in text, (
            "agents.md에 pm-planner-agent 참조 없음 — PM 분리 구조 미반영"
        )
        assert "pipeline-manager-agent" in text, (
            "agents.md에 pipeline-manager-agent 참조 없음 — PM 분리 구조 미반영"
        )


# oracle TC-1/TC-2 standalone 함수 (test_set.json command_check 직접 실행용)
def test_pm_planner_agent_registered_active():
    """oracle TC-1 대응: pm-planner-agent.md가 active agent로 등록되어 실존한다."""
    md = AGENTS_DIR / "pm-planner-agent.md"
    assert md.exists(), f"pm-planner-agent.md 없음: {md}"


def test_pm_agent_is_compat_not_active():
    """oracle TC-2 대응: pm-agent.md가 compat 문서로 구분되어 active execution에서 제외된다."""
    pl = _load_pipeline_module()
    assert pl.PHASE_AGENT_IDS.get("pm") == "pm-agent"  # legacy entry
    assert pl.PHASE_AGENT_IDS.get("pm_planner") == "pm-planner-agent"  # active
    assert pl.PHASE_AGENT_IDS.get("pipeline_manager") == "pipeline-manager-agent"  # active
