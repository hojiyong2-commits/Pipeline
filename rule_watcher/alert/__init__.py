# [Purpose]: rule_watcher.alert 패키지 export — AlertEngine + Notifier 인터페이스 공개.
# [Assumptions]: 이 패키지는 rule_watcher.db / rule_watcher.config / rule_watcher.engine에 의존.
# [Vulnerability & Risks]: 없음 — re-export only. 순환 import 위험 없음 (alert는 engine을 사용하지만 engine은 alert를 모름).
# [Improvement]: AlertEngine 싱글톤 팩토리 추가 (현재는 호출자가 직접 생성).
"""알림 엔진 패키지 — cooldown 기반 알림 트리거 + notifier 디스패치."""
from rule_watcher.alert.alert_engine import AlertEngine
from rule_watcher.alert.notifiers import (
    AlertPayload,
    BaseNotifier,
    InAppNotifier,
    NotifierRegistry,
    SoundNotifier,
)

__all__ = [
    "AlertEngine",
    "AlertPayload",
    "BaseNotifier",
    "InAppNotifier",
    "NotifierRegistry",
    "SoundNotifier",
]
