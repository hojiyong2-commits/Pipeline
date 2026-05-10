# EmailMonitor.exe 코드 점검 리포트

**파이프라인 ID:** IMP-20260510-D44F
**점검 일자:** 2026-05-10
**대상 파일:**
- `po_automation/email_monitor.py` (1645줄)
- `po_automation/main.py` (642줄)

**범위:** 읽기 전용 정적 분석. 코드 변경 없음. EXE 빌드 없음.

---

## 개요

본 리포트는 PO 이메일 자동화 모듈(EmailMonitor.exe)의 백엔드/프론트엔드 코드를 4개 영역(버그, 보안, 성능, 유지보수성)으로 점검한 결과입니다. 총 17개 발견 항목이 식별되었으며 각 항목은 정확한 파일 경로, 라인 번호, 코드 인용, 영향 시나리오, 권고 패치를 포함합니다.

대상 파일은 PyInstaller `--onefile --windowed` 빌드 대상으로, Outlook COM, pdfplumber/pymupdf, Tkinter GUI를 결합한 합성 코드 베이스입니다.

---

## 요약 테이블

| 심각도 | 버그 | 보안 | 성능 | 유지보수성 | 합계 |
|--------|------|------|------|------------|------|
| CRITICAL | 0 | 0 | 0 | 0 | 0 |
| HIGH | 1 | 2 | 1 | 1 | 5 |
| MEDIUM | 3 | 2 | 2 | 0 | 7 |
| LOW | 1 | 1 | 1 | 2 | 5 |
| **합계** | **5** | **5** | **4** | **3** | **17** |

심각도 분류 기준:
- **CRITICAL**: 즉시 데이터 손실/보안 침해 가능, 운영 차단 권고.
- **HIGH**: 사용자 영향 또는 데이터 무결성 영향, 이른 시일 내 수정 권고.
- **MEDIUM**: 특정 시나리오에서 결함 발현, 다음 릴리즈에 수정 권고.
- **LOW**: 방어적 개선/이론적 위험, 차후 개선 항목.

---

## 버그 발견 사항

### [HIGH] B1 — scan_past_emails: ReceivedTime None 메일이 since_dt 비교 우회

- **파일:** `po_automation/email_monitor.py`
- **위치:** L1200~L1219
- **설명:** `received = getattr(item, "ReceivedTime", None)` 후 `if received is not None:` 분기로 진입하므로, `received`가 None인 경우 비교 블록 전체를 건너뛰고 L1221 `_process_mail_item` 호출로 직행합니다. Outlook의 초안(Draft), 시스템 메시지, MAPI 동기화 미완료 메일에서 ReceivedTime이 None일 수 있어, since_dt 필터를 의도하는 사용자 기대와 어긋나게 since_dt 이전 또는 미정 메일이 처리될 수 있습니다.
- **권고:** 다음 패치를 적용하여 명시적으로 ReceivedTime None을 스킵 처리합니다.
  ```python
  if received is None:
      logger.warning("ReceivedTime None 메일 스킵: subject=%s", getattr(item, "Subject", ""))
      item = items.GetNext()
      continue
  ```
  의도적으로 None 메일을 처리해야 하는 경우라면 `_process_mail_item` docstring에 그 사유를 명시합니다.

### [MEDIUM] B2 — _process_mail_item rename 실패 분기에서 tmp 폴더 잔존

- **파일:** `po_automation/email_monitor.py`
- **위치:** L1058~L1072
- **설명:** PDF 파싱 후 폴더명을 prefix/amount/incoterm 등으로 결정하여 `tmp_dest_folder.rename(dest_folder)`로 이동합니다. 그러나 (1) `dest_folder`가 이미 존재하면 L1062에서 `pass`만 실행되어 임시 폴더(`root/po_number`)가 빈 채로 남고, (2) `rename()`이 OSError로 실패하면 L1070에서 `dest_folder.mkdir(...)`만 호출되어 임시 폴더 정리 누락. 동일 PO에 다중 메일이 누적되는 사용 패턴에서 빈 임시 폴더가 계속 쌓입니다.
- **권고:** L1062와 L1070 직후에 다음 코드를 추가합니다.
  ```python
  try:
      tmp_dest_folder.rmdir()  # 비어있을 때만 성공
  except OSError:
      pass
  ```

### [MEDIUM] B3 — log_queue 포화 시 silent drop

- **파일:** `po_automation/main.py`, `po_automation/email_monitor.py`
- **위치:** main.py L112, email_monitor.py L866~L867
- **설명:** main.py L112에서 `queue.Queue(maxsize=2000)`로 크기를 제한하고, email_monitor.py L865 `log_queue.put_nowait(msg)`가 `queue.Full` 예외 시 L866-L867에서 `logger.warning`만 남기고 메시지를 드롭합니다. 대량 PO 일괄 스캔(5000+ 메일) 또는 GUI 멈춤(`_poll_log_queue` 100ms 대기) 동안 큐가 포화되면 사용자가 GUI에서 누락을 인지할 수 없습니다.
- **권고:** 드롭 카운터를 도입하고 `_poll_log_queue`에서 비동기 알림을 추가합니다.
  ```python
  # email_monitor 측: log_queue.put_nowait 실패 시 드롭 카운터 증가 (전역 또는 핸들러 인스턴스)
  # main.py _poll_log_queue: 드롭 카운트 변화 감지 시 GUI에 "[경고] 로그 N건 드롭됨" 출력
  ```
  대안: `queue.put(msg, timeout=0.5)` 백프레셔(백엔드 잠시 멈춤) 또는 `maxsize=10000`로 상향.

### [MEDIUM] B4 — OnNewMailEx 내부 Dispatch와 _monitor_loop COM 비일관성

- **파일:** `po_automation/email_monitor.py`
- **위치:** L1332~L1333
- **설명:** `_monitor_loop`(L1407~L1448)에서 `pythoncom.CoInitialize()` 후 `DispatchWithEvents("Outlook.Application", OutlookEventHandler)`로 핸들러를 바인딩합니다. 그러나 OnNewMailEx 콜백 내부 L1332~L1333에서 `win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")`로 별도 Dispatch 인스턴스를 매번 신규 생성합니다. 동일 STA 안에서 호출되더라도 win32com.client gencache 캐시 동작에 따라 새 dispatch 객체가 생성되어 자원 낭비 + 잠재적 RPC 동기화 이슈가 발생할 수 있습니다.
- **권고:** DispatchWithEvents가 `self.Application`을 자동 주입하므로 다음 패턴을 사용합니다.
  ```python
  # OnNewMailEx 내부
  namespace = self.Application.GetNamespace("MAPI")
  mail_item = namespace.GetItemFromID(entry_id)
  ```
  또는 `_monitor_loop`에서 namespace를 미리 획득하여 `handler._namespace = namespace`로 주입.

### [LOW] B5 — _save_settings: tempfile fd 누수 가능성

- **파일:** `po_automation/main.py`
- **위치:** L564~L574
- **설명:** L564 `fd, tmp = tempfile.mkstemp(...)` 직후 L565 `try:` 블록 진입 직전에 `KeyboardInterrupt` 또는 `MemoryError`가 발생하면 fd가 닫히지 않습니다. 실 발생 가능성은 매우 낮으나 방어적 코드로 보강 가능합니다.
- **권고:** 다음 패턴으로 변경합니다.
  ```python
  fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
  try:
      with os.fdopen(fd, "w", encoding="utf-8") as f:
          f.write(content)
      os.replace(tmp, str(p))
  except Exception:
      try:
          os.close(fd)  # fdopen 실패 시 보강
      except OSError:
          pass
      try:
          os.unlink(tmp)
      except OSError:
          pass
      raise
  ```

---

## 보안 발견 사항

### [HIGH] S2 — 발신자 필터: GetExchangeUser 실패 시 빈 문자열 fallback

- **파일:** `po_automation/email_monitor.py`
- **위치:** L795~L799 (`_get_smtp_address`), L906~L913 (`_process_mail_item`)
- **설명:** `_get_smtp_address`는 Exchange DN 주소에 대해 `Sender.GetExchangeUser().PrimarySmtpAddress` 조회를 시도하고 실패하면 `addr`이 '@' 미포함이므로 L799 `return ""`로 종료합니다. 이후 L910-L913에서 `if sender_addr not in allowed_senders: return False`가 적용되어 fail-safe(필터 미통과)로 동작합니다. 그러나 (1) 사용자에게 silent drop 알림 없음, (2) `allowed_senders`에 우연히 빈 문자열이 포함되면 모든 DN 미해석 메일이 우회 통과될 위험.
- **권고:**
  - `allowed_senders` 정규화 시 빈 문자열을 자동 제거합니다.
  - `sender_addr == ""` 케이스를 별도 로그 카테고리(`WARN: SENDER_RESOLVE_FAILED`)로 출력하고 GUI에 통지하여 사용자가 인지 가능하게 합니다.

### [HIGH] S3 — 수신자 필터: Exchange DN 변환 누락 (발신자와 비대칭)

- **파일:** `po_automation/email_monitor.py`
- **위치:** L916~L953
- **설명:** L929-L931에서 `addr = (getattr(rec, "Address", "") or "").lower().strip()`로 직접 비교하며, 발신자 측에 적용된 `_get_smtp_address` 동등 처리가 없습니다. 조직 내부 메일에서 `rec.Address`가 '/o=ExchangeLabs/...' DN 형식이면 `allowed_recipients`(소문자 SMTP 주소)와 매칭 실패 → 정당한 수신자임에도 silent drop. 발신자 처리(S2)와 비대칭이라 사용자가 동일 메일 흐름을 두 가지 다른 정책으로 보게 됩니다.
- **권고:** 수신자 전용 헬퍼 `_get_smtp_address_from_recipient(rec)`를 신설하여 발신자와 동일한 fallback 체인을 적용합니다.
  ```python
  def _get_smtp_address_from_recipient(rec) -> str:
      try:
          ae = rec.AddressEntry
          if ae is not None:
              eu = ae.GetExchangeUser()
              if eu is not None:
                  smtp = getattr(eu, "PrimarySmtpAddress", "") or ""
                  if smtp:
                      return smtp.lower()
      except Exception:
          pass
      addr = (getattr(rec, "Address", "") or "").lower().strip()
      return addr if "@" in addr else ""
  ```

### [MEDIUM] S1 — _safe_resolve: Windows reparse point 한계

- **파일:** `po_automation/email_monitor.py`
- **위치:** L39~L70
- **설명:** `Path.resolve()` + `relative_to()` 조합은 표준 패턴이며 None/타입 가드도 적용되어 있습니다. 다만 (1) Windows junction, OneDrive cloud sync 폴더 같은 reparse point 처리가 OS와 Python 버전에 따라 일관되지 않고, (2) `allowed_root` 자체가 상대 경로면 cwd 변경 시 결과가 흔들립니다. 현재 호출자(`main.py`)가 절대 경로를 전달하므로 실 위험은 낮습니다.
- **권고:** docstring에 "allowed_root는 호출자가 미리 `.resolve()`로 절대화해야 한다"를 명시하고, Windows에서 junction이 감지되면 경고 로그를 남기는 옵션 추가를 검토합니다.

### [MEDIUM] S4 — 고정 .tmp 접미사 — 동시성 보호 없음

- **파일:** `po_automation/email_monitor.py`
- **위치:** L588 (`_save_email_msg`), L646 (`_save_attachments`), L733 (`save_processed_set`)
- **설명:** 세 곳 모두 `path.with_suffix(... + ".tmp")` 패턴으로 고정 접미사를 사용합니다. 동일 EntryID에 대해 OnNewMailEx 이벤트가 중복 fire되는 케이스, 다중 모니터링 인스턴스 실행 케이스에서 같은 .tmp 파일에 동시 SaveAs/write가 발생하면 부분 쓰기 → `os.replace()` 동시 실행 → 데이터 손상이 가능합니다. `main.py _save_settings`(L564)는 이미 `tempfile.mkstemp`를 사용하므로 같은 패턴을 일관 적용해야 합니다.
- **권고:** 세 헬퍼를 `tempfile.mkstemp(dir=parent, suffix=".tmp")` 기반으로 마이그레이션합니다. mkstemp는 OS 레벨에서 고유 이름을 보장하여 경쟁을 차단합니다.

### [LOW] S5 — _safe_filename: 200자 절단 + "untitled" fallback 충돌

- **파일:** `po_automation/email_monitor.py`
- **위치:** L77~L94
- **설명:** L94 `return sanitized[:200] if sanitized else "untitled"`는 200자 초과 동일 prefix 메일이 다수 도착하면 절단 결과가 동일해지고, 후속 `_get_unique_path`가 (1), (2), ... 카운터를 부여합니다. 동일 폴더에 수천 개 파일이 누적되면 디렉토리 스캔이 느려지고 기준 시각 누락 식별이 어렵습니다. "untitled" fallback도 동일 충돌 발생.
- **권고:** 절단 시 짧은 hash 접미사를 부여합니다.
  ```python
  import hashlib
  hash8 = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
  trimmed = sanitized[:190]
  return f"{trimmed}_{hash8}" if sanitized else f"untitled_{hash8}"
  ```

---

## 성능 발견 사항

### [HIGH] P1 — OnNewMailEx: 매 EntryID마다 Dispatch 신규 생성

- **파일:** `po_automation/email_monitor.py`
- **위치:** L1330~L1333
- **설명:** L1330 `for entry_id in entry_ids:` 안의 L1332 `namespace = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")`가 각 EntryID마다 새 COM 객체를 생성합니다. Outlook COM Dispatch는 RPC를 수반하여 수십~수백ms 비용이 발생하며, 1통 메일에 다수 EntryID가 포함된 케이스(예: 10개 EntryID 묶음 알림)에서 누적 지연이 가시화됩니다.
- **권고:** `OutlookEventHandler.__init__` 또는 `_monitor_loop`에서 namespace를 1회 획득하여 인스턴스 변수로 캐시합니다.
  ```python
  # _monitor_loop 안:
  namespace = handler.Application.GetNamespace("MAPI")
  handler._namespace = namespace
  
  # OnNewMailEx 안:
  for entry_id in entry_ids:
      mail_item = self._namespace.GetItemFromID(entry_id)
      ...
  ```

### [MEDIUM] P2 — scan_past_emails: 조기 종료(break) 미사용

- **파일:** `po_automation/email_monitor.py`
- **위치:** L1188 (Sort DESC), L1217~L1219 (continue)
- **설명:** L1188에서 `items.Sort("[ReceivedTime]", True)`로 내림차순 정렬한 후 L1217~L1219에서 `received_dt < since_dt` 발견 시 `continue`만 사용합니다. DESC 정렬 상태에서 since_dt 미만이 한 번 발견되면 이후 모든 메일은 더 오래된 메일이므로 `break`로 즉시 종료할 수 있는데, 현재 코드는 모든 메일을 끝까지 순회합니다. 1만+ 메일 inbox에서 명백한 비효율입니다.
- **권고:** `continue` → `break`로 변경합니다. 단 ReceivedTime 파싱 실패 케이스(L1214 except)는 `continue`를 유지해야 합니다.
  ```python
  if received_dt is not None and received_dt < since_dt:
      logger.info("DESC 정렬 + since_dt 미만 발견 — 조기 종료")
      break
  ```

### [MEDIUM] P3 — _parse_pdf_info: 전페이지 텍스트 메모리 누적

- **파일:** `po_automation/email_monitor.py`
- **위치:** L351~L378
- **설명:** L351 `full_text: str = ""` 후 L356-L358 (pymupdf), L370-L372 (pdfplumber)에서 페이지별 텍스트를 `full_text += page_text + "\n"`로 누적합니다. 100MB+ PDF / 1000페이지 이상에서 (1) RAM이 폭발하고, (2) Python 문자열 concat이 O(n²) 복잡도를 유발합니다. PyInstaller EXE는 메모리 제한이 더 빡빡한 데스크톱에서 실행되므로 영향이 큽니다.
- **권고:** `list.append()` 후 `"".join()` 패턴 또는 페이지별 즉시 검색 후 결과 캐시 패턴을 사용합니다.
  ```python
  parts: list[str] = []
  for page in doc:
      parts.append(page.get_text())
  full_text = "\n".join(parts)
  ```
  추가 최적화: Total Amount 검색은 페이지 단위로 수행하여 발견 시 early exit.

### [LOW] P4 — _poll_log_queue: 100ms 폴링

- **파일:** `po_automation/main.py`
- **위치:** L480~L493
- **설명:** L493 `self._root.after(100, self._poll_log_queue)`로 초당 10회 wakeup이 발생합니다. idle 시 노트북 배터리에 미미한 영향이 있으나 UI 응답성과 합리적 트레이드오프를 이루고 있습니다.
- **권고:** 현재 구조 유지 권장. 개선이 필요하면 200ms로 완화하거나 별도 스레드 + `queue.get(timeout=0.1)` 패턴 도입.

---

## 유지보수성 발견 사항

### [HIGH] M1 — _process_mail_item: 293줄 단일 함수, SRP 위반

- **파일:** `po_automation/email_monitor.py`
- **위치:** L806~L1098
- **설명:** 단일 함수 안에 6개 이상 책임이 혼재합니다.
  - L869~L905: 제목/본문 파싱 + PO 번호 추출
  - L906~L953: 발신자/수신자 필터
  - L955~L958: 중복 차단
  - L960~L1032: PDF 첨부 파싱
  - L1034~L1074: 폴더명 조합 + 생성
  - L1076~L1090: .msg/첨부 저장
  단위 테스트가 어렵고, 한 가지 책임을 변경할 때 회귀 위험이 누적되어 유지보수성이 크게 저하됩니다.
- **권고:** 다음 헬퍼로 분리합니다.
  ```python
  def _filter_by_sender(mail_item, allowed_senders) -> bool: ...
  def _filter_by_recipient(mail_item, allowed_recipients) -> bool: ...
  def _extract_po_metadata_from_pdfs(attachments, po_number, tmp_folder) -> tuple: ...
  def _build_dest_folder_name(po_number, prefix, amount, date, incoterm, ic) -> str: ...
  def _save_email_artifacts(mail_item, dest_folder, subject) -> list: ...
  ```
  분리 후 `_process_mail_item`은 각 헬퍼를 호출하는 ~80줄 오케스트레이터로 축소됩니다.

### [LOW] M2 — OutlookEventHandler: __init__ 외부 주입 패턴

- **파일:** `po_automation/email_monitor.py`
- **위치:** L1264~L1344
- **설명:** L1271~L1278에서 5개 핵심 변수(`_root_folder`, `_processed_set`, `_log_queue`, `_allowed_senders`, `_allowed_recipients`)를 None으로 초기화하고 `_monitor_loop`(L1424~L1428)에서 외부 주입합니다. `win32com DispatchWithEvents`가 `__init__`에 인자를 전달할 수 없는 제약 때문이며, L1314 `if self._root_folder is None or ...:` 가드로 부분적으로 방어합니다. 그러나 호출자가 주입을 잊으면 silent fail(early return + logger.error만) 위험이 남습니다.
- **권고:** 명시적 `set_context(root_folder, processed_set, ...)` 메서드를 노출하고, 미초기화 시 `RuntimeError`로 즉시 실패하도록 강화합니다.
  ```python
  def set_context(self, root_folder, processed_set, log_queue, allowed_senders, allowed_recipients):
      ...  # 모든 인자 None/타입 가드
  
  def OnNewMailEx(self, ...):
      if self._root_folder is None:
          raise RuntimeError("OutlookEventHandler.set_context() 미호출 상태에서 OnNewMailEx 발생")
      ...
  ```

### [LOW] M3 — _pdf_sort_key: 클로저 의존, 단위 테스트 불가

- **파일:** `po_automation/email_monitor.py`
- **위치:** L991~L1001
- **설명:** `_process_mail_item` 내부에 정의된 `_pdf_sort_key`는 외부 클로저 변수 `po_number`에 의존하여 단위 테스트가 어렵습니다. 정렬 우선순위 로직(po_number를 파일명에 포함하면 0, 아니면 1)은 PDF 파싱 정확도에 직결되므로 회귀 방지 테스트가 중요합니다.
- **권고:** 모듈 레벨 함수로 추출하고 `functools.partial`로 연결합니다.
  ```python
  def _pdf_sort_key(item: tuple, po_number: str) -> int:
      """sort key: po_number 포함 시 0, 아니면 1. None/빈문자열 방어."""
      name = item[0]
      if name is None or not isinstance(name, str) or len(name) == 0:
          return 1
      return 0 if po_number.lower() in name.lower() else 1
  
  # 호출자:
  sorted_pdfs = sorted(pdf_list, key=lambda item: _pdf_sort_key(item, po_number))
  ```
  분리 후 self-verify 블록에 assert 3~5개 추가 가능합니다.

---

## 권고사항

### 우선순위 1 — 즉시 수정 권고 (HIGH 5건)

1. **B1 ReceivedTime None 방어** (L1200-L1219): None 메일을 명시적 스킵하여 since_dt 필터 의도를 지킵니다.
2. **S2 발신자 fallback 알림** (L795-L913): silent drop을 GUI 경고로 격상하고 빈 문자열 정규화를 적용합니다.
3. **S3 수신자 DN 처리 비대칭 해소** (L916-L953): 발신자와 동일한 `_get_smtp_address_from_recipient` 헬퍼 도입.
4. **P1 Outlook namespace 캐싱** (L1330-L1333): OnNewMailEx Dispatch 재생성을 1회 획득으로 전환.
5. **M1 _process_mail_item 분리** (L806-L1098): 6개 헬퍼로 분해하여 SRP 회복.

### 우선순위 2 — 다음 릴리즈 수정 (MEDIUM 7건)

1. **B2 빈 임시 폴더 정리** (L1058-L1072): rename 실패/dest 존재 분기에서 `rmdir()` 호출.
2. **B3 log_queue 드롭 통지** (main.py L112): 드롭 카운터 + GUI 경고.
3. **B4 OnNewMailEx COM 일관성** (L1332-L1333): `self.Application` 사용으로 단일 STA 보장.
4. **S1 _safe_resolve docstring 보강** (L39-L70): allowed_root 절대화 명시.
5. **S4 .tmp 동시성 차단** (L588, L646, L733): `tempfile.mkstemp` 일괄 적용.
6. **P2 scan 조기 종료** (L1217-L1219): `continue` → `break`로 1만+ inbox 최적화.
7. **P3 PDF 메모리 누적 해소** (L351-L378): list+join 패턴 + 페이지별 early exit.

### 우선순위 3 — 차후 개선 (LOW 5건)

1. **B5 fd 누수 방어 보강** (main.py L564-L574): except 블록에 `os.close(fd)` 추가.
2. **S5 파일명 hash 접미사** (L77-L94): 200자 절단 + 8자 hash로 충돌 차단.
3. **P4 _poll_log_queue 200ms 완화** (main.py L480-L493): 선택 사항, 현재 합리적.
4. **M2 set_context 명시화** (L1264-L1344): `RuntimeError` 즉시 실패로 전환.
5. **M3 _pdf_sort_key 추출** (L991-L1001): 모듈 레벨 + functools.partial로 테스트 가능화.

### 영역별 종합 평가

- **버그 (5건):** B1이 가장 시급. 사용자가 "기준 시각 이전 메일이 처리됐다"고 보고할 가능성. B2-B4는 운영 환경에서 누적되며 점진적으로 표면화.
- **보안 (5건):** S2-S3는 조직 내부 메일 환경(Exchange DN)에서 즉시 영향. _safe_resolve 자체는 표준 패턴이라 추가 강화는 선택적.
- **성능 (4건):** P1이 가장 큰 비용 절감 효과. P2-P3는 대용량 시나리오 한정.
- **유지보수성 (3건):** M1이 향후 모든 변경 작업의 비용을 좌우. 우선순위 1로 격상 권고.

---

**총평:** 코드베이스는 자기반성 헤더 주석, 타입 힌팅, AL/FS/SEC 가드 패턴이 잘 적용되어 있어 기본 품질은 높습니다. 그러나 (1) `_process_mail_item` 거대 함수, (2) 발신자/수신자 처리 비대칭, (3) Outlook COM 객체 재생성으로 인한 성능 저하가 향후 확장성을 제약합니다. 위 17개 권고를 우선순위 순서로 적용하면 코드 품질, 사용자 신뢰성, 유지보수 비용 모두 단계적으로 개선될 것으로 판단됩니다.
