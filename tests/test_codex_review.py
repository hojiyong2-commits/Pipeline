"""tests/test_codex_review.py

IMP-20260612-8104: Codex Review Gate 완전 제거로 인해 이 파일의 모든 테스트가 무효화됨.

원본 파일 (IMP-20260516-A627 MT-5)에는 아래 TestCase가 포함됐으나
pipeline.py에서 해당 기능이 모두 제거되어 ImportError가 발생하므로
파일 내용을 비웁니다. 회귀 테스트는 tests/test_codex_removal_8104.py로 대체됩니다.

제거된 기능:
- _validate_codex_review_schema()
- cmd_review() (codex/codex-run/codex-record/status 서브커맨드)
- _check_codex_review_gate()
- cmd_review_codex_run()
- cmd_review_codex_record()
"""
# IMP-20260612-8104: 이 파일에는 더 이상 테스트가 없습니다.
# 대체 테스트: tests/test_codex_removal_8104.py (Codex Review Gate 제거 회귀 검증)
