# [Purpose]: rule_watcher 패키지의 진입점 및 버전 정보 제공
# [Assumptions]: 패키지 내부 모듈에서 __version__ 참조 시 접근 가능
# [Vulnerability & Risks]: 없음 — 단순 상수 정의
# [Improvement]: setup.py / pyproject.toml과 자동 동기화
"""한국주식 Rule Watcher — 룰 기반 종목 감시 앱 v1.0"""
__version__ = "1.0.0"
