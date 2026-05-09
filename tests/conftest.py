"""
conftest.py
-----------
pytest 설정 — ic_part_src/ 를 sys.path에 추가하여 order_mapper, automation import 가능하게 합니다.
"""
import sys
from pathlib import Path

# ic_part_src 디렉토리를 sys.path 최우선 위치에 삽입
_IC_PART_SRC = Path(__file__).resolve().parent.parent / "ic_part_src"
if str(_IC_PART_SRC) not in sys.path:
    sys.path.insert(0, str(_IC_PART_SRC))
