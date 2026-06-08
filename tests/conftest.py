"""
conftest.py
-----------
pytest 설정 — ic_part_src/ 와 프로젝트 루트를 sys.path에 추가합니다.
ic_part_src: order_mapper, automation import용
프로젝트 루트: core.acceptance, pipeline 등 프로젝트 패키지 import용
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_IC_PART_SRC = _PROJECT_ROOT / "ic_part_src"

# 프로젝트 루트를 sys.path에 추가 (core.acceptance 등 import용)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ic_part_src 디렉토리를 sys.path 최우선 위치에 삽입
if str(_IC_PART_SRC) not in sys.path:
    sys.path.insert(0, str(_IC_PART_SRC))
