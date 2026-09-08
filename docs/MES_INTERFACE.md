# MES 연계 인터페이스 규격서

> AIVIS(AI 머신비전 품질검사 시스템) ↔ MES 품질관리 연계
> 대상 독자: **MES 측 담당자 / SI 업체**. 이 문서만으로 수신부를 구현할 수 있게 썼다.
> 근거: CLAUDE.md §5 M9, §7.3 · 구현: `services/api/mes/*`

---

## 0. 한 장 요약

- AIVIS 는 제품 1개를 검사할 때마다 **판정 결과 1건**을 MES 로 넘긴다.
- 연계 방식은 두 가지이고 **설정 한 줄로 전환**된다. 기본은 ①.
  - ① **DB 인터페이스 테이블** — AIVIS 가 `mes_quality_if` 에 INSERT, MES 가 폴링해 가져간다.
  - ② **REST** — AIVIS 가 MES 의 엔드포인트로 POST 한다.
- 중복 적재는 **멱등키**로 막는다: `lot | item_code | inspected_at | cam_id`.
- 실패한 건은 사라지지 않는다. `mes_synced=false` 로 남아 워치독이 지수 백오프로 재시도한다(연계율 100% 목표, §1.2).

**MES 측이 결정해야 하는 것은 두 가지뿐이다**: (1) ① 또는 ② 중 무엇을 쓸지, (2) ①이면 접속 정보, ②면 엔드포인트 URL·인증 방식.

---

## 1. 어느 방식을 고를 것인가

| | ① DB 인터페이스 테이블 (기본·권장) | ② REST |
|---|---|---|
| 방향 | MES 가 가져간다(pull) | AIVIS 가 보낸다(push) |
| 네트워크 단절 시 | 테이블에 그대로 남음. 복구 후 MES 가 이어서 폴링 | 재시도 큐에 남음. 복구 후 자동 재전송 |
| MES 측 개발량 | 폴링 쿼리 + `consumed` 표시 | 수신 엔드포인트 1개 |
| AIVIS 장애 시 | 이미 적재된 건은 MES 가 그대로 처리 | 미전송분은 AIVIS 복구 후 전송 |
| 권장 상황 | **현장 표준.** 공장망이 불안정하거나 MES 가 배치 처리 | MES 가 실시간 수신 API 를 이미 갖춘 경우 |

§7.3 의 우선순위대로 **①을 기본값**으로 둔다. 설정값이 잘못되어도 ①로 폴백한다(연계가 조용히 멈추는 것보다 낫다).

OPC-UA / Modbus 는 이번 범위가 아니다(향후 확장).

---

## 2. 방식 ① — DB 인터페이스 테이블

### 2.1 테이블 정의 `mes_quality_if`

AIVIS 가 검사결과를 저장하는 **같은 트랜잭션에서** 이 테이블에 INSERT 한다. 검사결과는 남았는데 연계 행만 빠지는 상태가 생기지 않는다.

| 컬럼 | 타입 | NULL | 설명 |
|---|---|---|---|
| `id` | BIGSERIAL PK | N | 스테이징 일련번호 |
| `inspection_id` | BIGINT | Y | AIVIS 검사결과 id(역추적용) |
| `lot` | TEXT | N | LOT 번호 |
| `item_code` | TEXT | Y | 품목코드 |
| `inspected_at` | TIMESTAMPTZ | N | 검사시각(UTC) |
| `cam_id` | TEXT | N | 카메라/스테이션 식별자 |
| `idem_key` | TEXT **UNIQUE** | N | 멱등키(§3) |
| `work_order` | TEXT | Y | 작업지시 번호 |
| `final_verdict` | TEXT | N | `OK` \| `NG` |
| `defect_codes` | TEXT[] | Y | 불량유형 배열(§4) |
| `meas_length_mm` | NUMERIC(10,3) | Y | 측정 길이(mm) |
| `deviation_mm` | NUMERIC(10,3) | Y | 기준 대비 편차(mm) |
| `consumed` | BOOLEAN | N | **MES 가 가져갔음** 표시(기본 false) |
| `retry_count` | INT | N | 재시도 횟수(AIVIS 가 관리) |
| `created_at` | TIMESTAMPTZ | Y | 적재 시각 |

### 2.2 MES 측 처리 절차

```sql
-- 1) 미처리분 조회 (오래된 것부터, 한 번에 적당량)
SELECT * FROM mes_quality_if
 WHERE consumed = false
 ORDER BY id
 LIMIT 500;

-- 2) MES 품질이력에 적재 (MES 트랜잭션)

-- 3) 가져갔음을 표시  ※ 반드시 2)가 커밋된 뒤에
UPDATE mes_quality_if SET consumed = true WHERE id = ANY(:ids);
```

**주의 세 가지**

1. `consumed=true` 표시는 **MES 적재가 확정된 뒤**에 한다. 먼저 표시하고 적재에 실패하면 그 건은 영영 사라진다.
2. 같은 `idem_key` 가 다시 보이면 **중복이므로 무시**한다(§3). UNIQUE 제약이 있어 정상 상황에서는 생기지 않지만, MES 측 재처리 시 방어가 필요하다.
3. 폴링 주기는 MES 가 정한다. 검사 템포가 1.5초 내외이므로 **10~60초** 권장.

행 삭제는 MES 가 하지 않는다. 보관·정리는 AIVIS 측 정책이다.

---

## 3. 방식 ② — REST

### 3.1 요청

```
POST <MES_REST_URL>
Content-Type: application/json
X-Idempotency-Key: <멱등키>        ← 헤더명은 설정 가능(MES_IDEM_HEADER)
```

```json
{
  "inspection_id": 128374,
  "lot": "L20260908-03",
  "item_code": "HP12",
  "inspected_at": "2026-09-08T02:14:33.482000+00:00",
  "cam_id": "CAM1",
  "idem_key": "L20260908-03|HP12|2026-09-08T02:14:33.482000+00:00|CAM1",
  "work_order": "WO-2026-0912",
  "final_verdict": "NG",
  "defect_codes": ["SCR"],
  "meas_length_mm": 125.412,
  "deviation_mm": 0.412
}
```

멱등키는 **헤더와 본문 양쪽**에 실린다. MES 는 편한 쪽을 쓰면 된다.

### 3.2 응답 계약

| MES 응답 | AIVIS 동작 |
|---|---|
| `2xx` / `3xx` | 성공. `mes_synced=true` 로 표시하고 재시도하지 않음 |
| `4xx` / `5xx` | 실패. 재시도 큐에 남아 지수 백오프로 재전송 |
| 무응답·타임아웃 | 실패와 동일 처리 |

본문 형식은 제약하지 않는다(JSON 이 아니어도 무방). **HTTP 상태코드만 본다.**

**중복 요청은 정상 동작이다.** 네트워크가 끊겼다가 복구되면 같은 `idem_key` 가 다시 올 수 있다. MES 는 이미 처리한 키를 받으면 다시 적재하지 말고 `200` 을 돌려주면 된다(에러로 응답하면 AIVIS 가 계속 재시도한다).

### 3.3 AIVIS 자체 수신 엔드포인트 (참고)

AIVIS 에도 같은 규격의 수신부가 있다 — `POST /mes/quality`. 통합 전 MES 없이 연계 흐름을 검증하거나, 다른 AIVIS 스테이션의 결과를 모을 때 쓴다. 중복 키는 `{"status": "duplicate"}` 로 응답한다.

---

## 4. 데이터 사전

### 4.1 판정 `final_verdict`

`OK` = 양품, `NG` = 불량. 그 외 값은 오지 않는다.

### 4.2 불량유형 `defect_codes` (배열, §7.2)

| 코드 | 뜻 |
|---|---|
| `LEN` | 길이 부적합(기준 길이·공차 이탈) |
| `OIL` | 유분기(세척 후 유분·오염 잔존) |
| `DIS` | 변색 |
| `SCR` | 스크래치 |
| `MULTI` | 2종 이상 복합 |

**배열인 이유**: 한 제품에 두 가지 결함이 동시에 있을 수 있다. 복합불량은 개별 코드가 모두 들어가고 `MULTI` 가 함께 붙는다 — 예: `["OIL","DIS","MULTI"]`. MES 가 단일 코드만 저장한다면 배열의 첫 값이 아니라 **`MULTI` 여부를 먼저 확인**하는 편이 안전하다.

`final_verdict="OK"` 이면 배열은 빈 배열이다.

### 4.3 시각

모든 시각은 **UTC, ISO 8601**(`2026-09-08T02:14:33.482000+00:00`). 한국시간 표기는 MES 측에서 +9h 변환한다.

---

## 5. 멱등성 — 중복 적재를 막는 방법

멱등키는 네 값을 `|` 로 이은 문자열이다.

```
{lot}|{item_code}|{inspected_at ISO}|{cam_id}
```

같은 스테이션(`cam_id`)에서 같은 순간(`inspected_at`)에 두 제품을 검사할 수 없으므로 이 조합은 제품 1개를 유일하게 가리킨다. 검사 id 를 쓰지 않는 이유는, AIVIS 를 재설치하면 id 가 다시 1부터 시작해 과거 데이터와 충돌할 수 있기 때문이다.

- ① 테이블 방식: `idem_key` 에 **UNIQUE 제약**이 걸려 있어 중복 INSERT 자체가 불가능하다.
- ② REST 방식: MES 가 키를 기억해 중복을 무시한다(§3.2).

---

## 6. 연계율 100% 를 보장하는 방법

인수 기준(§1.2)의 "데이터 저장 & MES 연계율 100%" 는 선언이 아니라 아래 장치로 지킨다.

1. **미전송 표시** — 연계되지 않은 검사행은 `inspection.mes_synced = false` 로 남는다. 성공해야만 true 가 된다.
2. **워치독** — 주기적으로 `mes_synced=false` 인 행을 모아 재전송한다. 기본 10초 주기, 100건 배치.
3. **지수 백오프** — 실패할 때마다 대기가 0.5초 → 1 → 2 → 4 … 최대 30초로 늘어난다. MES 가 잠시 멈춰도 요청 폭주로 더 밀어붙이지 않는다.
4. **재시도 상한** — 기본 8회를 넘기면 실패로 표시하고 `sys_log`(category=`mes`)에 남긴다. 무한 재시도로 큐가 막히는 것을 막는다.
5. **모니터 노출** — 대시보드 모니터 화면이 연계 대기 건수를 표시한다. `GET /system/status` 의 `mes_pending` 이 계속 늘면 연계가 끊긴 것이다.

**MES 를 계획 정지할 때**도 그대로 두면 된다. 정지 중 검사분은 `mes_synced=false` 로 쌓였다가 MES 가 돌아오면 자동으로 따라붙는다.

---

## 7. 설정 항목

AIVIS 측 환경변수. MES 담당자는 굵게 표시된 두 개만 정해 주면 된다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| **`MES_MODE`** | `table` | **`table`** 또는 **`rest`** |
| **`MES_REST_URL`** | (없음) | rest 모드의 MES 수신 URL |
| `MES_IDEM_HEADER` | `X-Idempotency-Key` | 멱등키 헤더명(MES 규약에 맞춰 변경) |
| `MES_REST_TIMEOUT_S` | `5.0` | 요청 타임아웃(초) |
| `MES_WATCHDOG_INTERVAL_S` | `10.0` | 재전송 워치독 주기(초) |
| `MES_WATCHDOG_BATCH` | `100` | 1회 재시도 배치 크기 |
| `MES_MAX_RETRY` | `8` | 행별 최대 재시도 |
| `MES_BACKOFF_BASE_S` | `0.5` | 백오프 기준(초) |
| `MES_BACKOFF_MAX_S` | `30.0` | 백오프 상한(초) |

① 테이블 방식은 AIVIS 와 MES 가 **같은 PostgreSQL** 을 보거나, MES 가 AIVIS DB 에 읽기/부분쓰기 계정으로 접속하는 형태를 전제한다. 별도 DB 를 써야 한다면 스테이징 테이블 복제 방식을 별도 협의한다.

---

## 8. 통합 점검 순서 (연계 시험 시나리오)

MES 담당자와 함께 아래를 순서대로 확인하면 연계 시험이 끝난다.

| # | 확인 항목 | 기대 |
|---|---|---|
| 1 | 검사 1건 발생 | ① `mes_quality_if` 에 1행 / ② MES 가 1건 수신 |
| 2 | 판정값 일치 | `final_verdict`, `defect_codes`, `meas_length_mm` 가 AIVIS 화면과 동일 |
| 3 | 복합불량 | `["OIL","DIS","MULTI"]` 형태가 MES 에 손실 없이 저장 |
| 4 | **중복 방지** | 같은 건을 재전송해도 MES 품질이력이 늘지 않음 |
| 5 | **단절 복구** | MES/네트워크를 5분 정지 → 그동안 검사분이 복구 후 자동 연계 |
| 6 | 연계율 | `GET /kpi/summary` 의 저장·MES 연계율 100% |
| 7 | 실패 로그 | 의도적 실패(잘못된 URL) 시 `sys_log` category=`mes` 에 기록 |

4·5번이 이 인터페이스의 핵심이다. 나머지는 통과하는데 그 둘이 안 되면 연계율 100% 는 달성되지 않는다.

---

## 9. 아직 정해지지 않은 것

- **연계 상대 MES 가 확정되지 않았다.** 위 규격은 양쪽 모드 모두 구현·시험을 마쳤지만, 실제 MES 와의 통합 시험은 상대가 정해진 뒤에 가능하다.
- MES 가 요구하는 **인증 방식**(API 키/토큰/mTLS)은 미정이다. 현재 REST 모드는 멱등키 헤더만 보낸다. 인증이 필요하면 헤더 추가로 대응한다.
- MES 측 **품질이력 테이블 컬럼 매핑**은 상대 스키마를 받아 확정한다. 위 §4 데이터 사전이 AIVIS 가 제공할 수 있는 전부다.

---

*구현: `services/api/mes/` (config / transport / adapter / watchdog), 스테이징 테이블 정의: `services/api/db/models.py` `MesQualityIf`.*
