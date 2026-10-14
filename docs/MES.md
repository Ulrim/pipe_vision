# AIVIS MES — 품질·생산실적 관리

> 결정(2026-10-14): 도입기업에 연계할 MES 가 없다. 그래서 인수 지표 4 "데이터 저장 & MES
> 연계율 100%" 를 **상대가 실제로 받았는지**로 보일 수 있도록 **품질·생산실적 범위의 MES 를
> 직접 만든다.** 개발지침 §2.2 는 "MES 솔루션 본체" 를 범위 밖으로 두었으므로, 이것은
> **범위를 넓힌 결정**이다. 공정 라우팅·설비 가동·자재/재고·BOM 은 여전히 범위 밖이다.
>
> 구현: `services/mes`(별도 서비스) · 화면: 대시보드 메뉴 "MES …" · 연계 규격: `docs/MES_INTERFACE.md`

---

## 1. 한 장 요약

- AIVIS(검사)와 **별도 서비스**다. 자기 DB·자기 API(:8100)를 갖고, AIVIS 와는 **연계
  인터페이스로만** 주고받는다(① `mes_quality_if` 테이블 폴링, ② REST 수신). 나중에 상용
  MES 를 들여도 AIVIS 는 바뀌지 않는다 — MES 쪽만 갈아끼우면 된다.
- MES 가 연계 테이블에서 검사결과를 가져가며 `consumed=true` 로 표시한다. 이 표시가 AIVIS
  KPI·월간 리포트의 **'MES 수신 확인'** 이다. 연계율이 '자기 테이블 적재율' 이라는 10/10
  점검 지적에 대한 답이다.
- 로그인은 하나 — MES 는 AIVIS 가 발급한 토큰을 같은 서명키(`JWT_SECRET`)로 검증하고
  역할(작업자 < 품질관리자 < 관리자)을 그대로 쓴다.
- 하는 일: **수주 → 작업지시(LOT) → 검사결과 수신 → 생산실적(자동 집계) → 출하 → 클레임 →
  월 KPI(리드타임·Claim·출하유출)**.

```
 [AIVIS 검사워커] → POST /inspection → [AIVIS API] ─ inspection + mes_quality_if (한 트랜잭션)
                                                         │
                         ① 테이블 폴링(기본, 10초) ──────┘  consumed=true 로 표시
                         ② REST (AIVIS MES_MODE=rest → POST /intake/quality)
                                                         ▼
                                              [MES :8100] 자체 DB
                         수주·작업지시·품질이력·출하·클레임 → 생산실적·월 KPI
                                                         ▲
                                [대시보드 MES 메뉴] — 같은 로그인 토큰
```

## 2. 화면 (대시보드 메뉴)

| 메뉴 | 하는 일 |
|---|---|
| **MES 작업지시** | 수주 등록·마감, 작업지시 등록(수주를 고르면 품목·남은 수량을 채운다, LOT 은 비우면 자동). 시작·완료·취소. **"검사기에 오더 내리기"** — AIVIS 현재 오더(품목·LOT·작업지시)를 바꾼다(워커가 15초 안에 전환, 전역 검사 모드는 그대로). 지금 검사기에 걸린 작업지시는 "◆ 검사기 적용 중" |
| **MES 생산실적** | 작업지시별 양품·불량·진척률, 단계별 표(검사·AI 불량·최종 불량·재확인·양품), 불량유형, 크레이트 개수. 아래에 **작업지시 없는 LOT**(그 LOT 으로 작업지시를 만들면 자동으로 이어진다) |
| **MES 출하·클레임** | 월 KPI 카드, 출하 등록(리드타임·납기 준수 표시), 클레임 등록·종결, **"AIVIS KPI 에 반영"** |
| **MES 연계현황** | 테이블 연계 켜짐/꺼짐, 마지막 폴링, **검사기에서 아직 안 가져간 건수**, 오류, 수신 기록, 최근 품질이력. "지금 가져오기" |

권한: 조회는 작업자 이상. 등록·수정은 품질관리자 이상. 작업지시 **시작·완료·취소는 작업자도**
한다(라인에서 하는 일). 수량·계획일은 품질관리자 이상 — AIVIS 오더 교체와 같은 원칙
(권한을 넓히는 대신 범위를 좁힌다).

## 3. 데이터 (`services/mes/mes_app/models.py`, 모두 `mes_` 접두어)

| 테이블 | 뜻 | 핵심 컬럼 |
|---|---|---|
| `mes_sales_order` | 수주 | so_no(비우면 `SO-YYYYMMDD-NNN`), 고객, 품목, 수량, 수주일, 납기, OPEN/CLOSED |
| `mes_work_order` | 작업지시 | wo_no(`WO-…`), so_id, 품목, **lot(UNIQUE, 비우면 `L-YYYYMMDD-NN`)**, 지시수량, 계획일, PLANNED→RUNNING→DONE/CANCELLED |
| `mes_quality_record` | 받은 검사결과 1건 | **idem_key(UNIQUE)**, lot, work_order, 단계, 튜브 번호, 판정, 불량유형, 길이, 재확인, 크레이트 개수, 수신 방식(table/rest) |
| `mes_shipment` | 출하 | ship_no(`SH-…`), so_id, LOT, 품목, 고객, 수량, 출하일 |
| `mes_claim` | 고객 클레임 | claim_no(`CL-…`), 고객, LOT, 부적합 수량, 유형(LEN/OIL/DIS/SCR/COUNT/ETC), 발생일, OPEN/CLOSED |
| `mes_intake_run` | 수신 기록 | 시각, 방식, 신규, 갱신, 오류(받은 것이 있거나 오류난 회차만, 최근 500) |

- **작업지시 ↔ 검사결과**: 작업지시 번호가 같거나, 번호 없이 **LOT 이 같은** 결과. 그래서
  작업지시마다 LOT 은 하나(UNIQUE). 검사기에 작업지시 번호를 안 넣어도 LOT 으로 이어진다.
- 첫 검사결과가 들어오면 작업지시가 **자동으로 RUNNING**(시작 시각 = 첫 검사). 결과가 먼저
  와 있던 LOT 으로 작업지시를 만들면 만들 때 바로 RUNNING.
- 시각은 모두 UTC 로 저장하고 응답에 `+00:00` 을 붙인다(sqlite 도 — 브라우저가 9시간 어긋나게
  읽지 않게). 날짜(수주일·출하일·발생일)는 사람이 입력한 현지 날짜.

## 4. 생산실적 — 어떻게 세나 (`mes_app/production.py`)

다발 튜브는 스테이션 사이에서 낱개로 추적되지 않는다(같은 튜브가 길이 검사와 표면 검사에서
각각 한 행). 그래서 **단계를 더하지 않고 단계별로 센다.**

| 값 | 정의 |
|---|---|
| 최종 판정 | 재확인 결과가 있으면 그것, 없으면 AI 판정 |
| 단계별 | 검사 수 · AI 불량 · **최종 불량** · 재확인 수 · 양품(검사 − 최종 불량) · 불량유형별 |
| 불량유형 | 최종 NG 행의 코드(MULTI 제외). AI 는 OK 였는데 재확인 NG 면 **MANUAL**(재확인 NG) |
| 불량 합계 | 단계별 최종 불량의 합 — 각 스테이션에서 걸러 낸 튜브는 다음 공정으로 가지 않는다 |
| **양품(실적)** | 크레이트 **개수 확인 합계**(실제로 담긴 수)가 있으면 그것, 없으면 마지막 검사 단계(표면 → 길이 순)의 양품 |
| 진척률 | 양품 ÷ 지시수량 × 100 |
| 크레이트 | 판 수 · 담긴 개수 합 · 개수 불일치 판 수(개수 확인 행은 제품 불량에 넣지 않는다) |

생산실적은 **저장하지 않고 매번 센다.** 재확인 결과가 나중에 바뀌면(AIVIS 가 같은 멱등키로
다시 내놓는다) 저장해 둔 실적이 틀어지기 때문이다.

## 5. 월 KPI — 손으로 넣던 값을 기록에서 (`mes_app/kpi.py`)

| 값 | 근거 | AIVIS KPI 항목 |
|---|---|---|
| 출하수량 | 그 달 출하일의 출하 수량 합 | `shipped_qty` |
| 리드타임(일) | 그 달 출하 중 수주에 이어진 것의 (출하일 − 수주일) 평균 | `lead_time_days` |
| 납기 준수율(%) | 납기가 있는 수주의 출하 중 출하일 ≤ 납기 비율 | (MES 화면만) |
| Claim 건수 | 그 달 발생일의 클레임 수 | `claim_count` |
| 출하유출 부적합 수량 | 그 달 발생 클레임의 부적합 수량 합 | `leak_defect_qty` |
| 출하유출불량률(ppm) | 부적합 ÷ 출하수량 × 1,000,000 | (AIVIS 가 같은 식으로 산출) |

**"AIVIS KPI 에 반영"** 버튼이 AIVIS `POST /kpi/manual` 로 위 **네 값만** 보낸다. AIVIS 는
2026-10-14 부터 **보낸 항목만 바꾸므로** 작업공수 지수·비고는 그대로다. 그 달 리드타임 표본이
없으면 `lead_time_days` 는 null 로 가서 그 항목을 비운다(화면에 안내).

## 6. API (`services/mes/mes_app/routers/*`, 모두 `Authorization: Bearer <AIVIS 토큰>`)

### 6.1 수주·작업지시
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/orders?status=` | operator+ | 수주 목록(+출하수량, 작업지시 수) |
| POST | `/orders` | quality+ | 수주 등록(번호 비우면 자동, 중복 409) |
| PATCH | `/orders/{id}` | quality+ | 상태(마감)·수량·납기·비고 |
| GET | `/work-orders?status=PLANNED,RUNNING` | operator+ | 작업지시 목록 + 실적 요약(양품·불량·진척률·마지막 검사) |
| POST | `/work-orders` | quality+ | 등록(수주를 고르면 품목 상속, LOT 중복 409) |
| GET | `/work-orders/{id}` | operator+ | `{work_order, production}` — 생산실적 상세 |
| PATCH | `/work-orders/{id}` | operator+(상태) / quality+(수량·계획일·비고) | 시작·완료·취소 |
| GET | `/production/unassigned` | operator+ | 작업지시 없는 LOT 의 실적 |

### 6.2 출하·클레임·KPI
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET/POST | `/shipments?period=YYYY-MM` | operator+ / quality+ | 출하(수주를 고르면 고객·품목 상속, 리드타임·납기 준수 포함) |
| GET/POST | `/claims?status=` | operator+ / quality+ | 클레임 |
| PATCH | `/claims/{id}` | quality+ | 종결(closed_at 기록)·수량·유형·내용 |
| GET | `/kpi/monthly?period=YYYY-MM` | operator+ | 월 KPI(§5). 비우면 이번 달(KST) |

### 6.3 수신·연계
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/intake/quality` | 수신 토큰(`MES_INTAKE_TOKEN`, 비우면 무인증) | AIVIS REST 연계 수신. 같은 키는 **갱신**하고 200 `{status: stored\|updated\|duplicate}` |
| POST | `/intake/poll` | quality+ | 지금 가져오기(테이블 연계가 꺼져 있으면 409) |
| GET | `/intake/status` | operator+ | 연계 현황(폴러 상태·마지막 결과·인터페이스 미수신 건수·오류·수신 기록) |
| GET | `/quality?lot=&work_order=&limit=` | operator+ | 받은 품질이력(추적) |
| GET | `/health` | 공개 | `{status, db, table_intake, service:"mes"}` |

## 7. 수신 — 테이블 폴링의 규칙 (`mes_app/intake.py`)

1. `consumed=false` 인 행을 id 순으로 최대 500건 읽는다(`MES_POLL_BATCH`).
2. MES 품질이력에 upsert 하고 **커밋한다.**
3. **커밋된 뒤에만** `consumed=true`(+`consumed_at`)로 표시한다. 먼저 표시하고 적재에 실패하면
   그 건은 영영 사라진다.
4. 읽은 뒤 AIVIS 가 그 행을 고쳤으면(재확인 → `updated_at` 변경) **표시하지 않는다.** 다음
   회차에 새 값으로 다시 읽는다 — 재확인 값이 MES 로 안 가는 일을 막는다.
5. 같은 멱등키는 **갱신**한다(재확인·작업지시 보정). 예전 AIVIS 가 안 보내는 값(None)으로
   덮어쓰지는 않는다.
6. 인터페이스 테이블은 **반영(reflection)** 으로 읽는다 — 새 컬럼이 없는 예전 AIVIS 도 동작한다.
7. 표시에 실패하면(잠김 등) 다음 회차에 다시 읽혀 `duplicate` 로 끝난다(적재는 한 번).

## 8. 설치·설정

### 8.1 환경변수 (`mes_app/config.py`)
| 변수 | 기본값 | 설명 |
|---|---|---|
| `MES_DATABASE_URL` | `sqlite:///./mes.db` | MES 자체 DB. 독립형은 `$AIVIS_HOME/db/mes.db`, compose 는 볼륨 sqlite(바꾸려면 Postgres URL) |
| `MES_AIVIS_DB_URL` | (없음) | AIVIS DB(연계 테이블). **비우면 폴링을 끄고 REST 수신만** 받는다 |
| `MES_POLL_INTERVAL_S` | `10` | 폴링 주기(초) |
| `MES_POLL_BATCH` | `500` | 1회 최대 건수 |
| `MES_POLLER_ENABLED` | `true` | 백그라운드 폴러(끄면 "지금 가져오기"로만) |
| `MES_INTAKE_TOKEN` | (없음) | REST 수신 토큰(AIVIS `MES_REST_TOKEN` 과 같은 값) |
| `JWT_SECRET` / `JWT_ALGORITHM` | AIVIS 와 같게 | **AIVIS 와 다르면 MES 화면이 "MES 가 로그인을 받지 않습니다"** |
| `ALLOWED_ORIGINS` | `*` | CORS |

### 8.2 독립형 파이 (`scripts/aivis-standalone.sh`)
API 와 함께 자동으로 뜬다(`AIVIS_RUN_MES=true`, `MES_PORT=8100`). API venv 를 그대로 쓰고,
연계 DB 는 AIVIS sqlite 파일이다(두 프로세스가 한 파일을 열므로 15초 잠김 대기를 둔다).
대시보드(:5174)는 같은 호스트의 :8100 으로 MES 를 부른다.

### 8.3 docker compose
`mes` 서비스(:8100, 볼륨 `mes-data`). 연계 DB 는 compose 의 Postgres.

### 8.4 80/443 역프록시 · 클라우드
- 대시보드를 80/443 으로 서빙하면 MES 는 같은 출처의 **`/mes-api/`** 로 부른다 → 역프록시에
  `location /mes-api/ { proxy_pass http://mes:8100/; }` 를 둔다. 없으면 MES 화면이 "MES 서버에
  연결할 수 없습니다" 를 띄운다(다른 화면은 멀쩡하다).
- 클라우드 대시보드는 `VITE_MES_API_BASE=https://<mes 주소>` 로 빌드한다(`apps/dashboard/.env.example`).

### 8.5 스키마 변경
MES 테이블은 기동 시 `create_all`(없는 테이블만 만든다)로 만든다. 컬럼을 추가하게 되면
AIVIS 의 `reconcile_sqlite_schema` 처럼 추가 컬럼 보정 또는 Alembic 을 도입한다(현재 v1).

## 9. 이번에 AIVIS 쪽에서 함께 고친 것 (2026-10-14)

| 무엇 | 왜 |
|---|---|
| **워치독이 어디서도 기동되지 않았다** → API 프로세스 안에서 뜬다(`MES_WATCHDOG_ENABLED`, 기본 true) | rest 모드는 아무것도 보내지 않았고, 로컬 큐 복구분은 스테이징되지 않았다 |
| table 모드: **스테이징과 같은 트랜잭션에서 `mes_synced=true`** | 문서상 정의('스테이징 적재 = 연계됨')를 코드가 지키지 않아 연계율이 0% 로 남았다(워치독이 없었으므로) |
| 연계 테이블 컬럼 추가: 단계·튜브 번호·재확인·크레이트 개수·`consumed_at`·`updated_at` (0012) | MES 가 연계값만으로 단계별 실적·재확인 반영 양품·포장 수량을 센다 |
| `inspection.count_detected/count_expected` (0012) + 워커 개수 모드가 기록 | 크레이트 개수가 하트비트로만 나가 DB 에 남지 않았다 — 포장 실적의 근거 |
| 재확인 → 연계 행 되돌림(table: `consumed=false`·`updated_at`, rest: 재전송) | 재확인으로 양품 수량이 바뀐다 |
| `POST /kpi/manual` 보낸 항목만 갱신 | MES 반영이 작업공수·비고를 지우지 않게(KPI 입력 화면이 비고를 매번 지우던 것도 함께 고쳐짐) |
| `PUT /master/active` 모드는 보낼 때만 변경 | "검사기에 오더 내리기" 가 전역 검사 모드를 기본값으로 지우지 않게 |
| REST 연계에 `MES_REST_TOKEN` → `Authorization: Bearer` | MES 수신 토큰 |

## 10. 검증

- `services/mes/tests` — 수신(표시 순서·재확인 재수신·읽는 중 변경·예전 테이블·REST·토큰),
  생산실적(단계별·재확인·크레이트·작업지시 매칭·미배정 LOT), 출하·클레임·월 KPI, 권한, UTC.
- `tests/e2e/test_mes_e2e.py` — **AIVIS → MES 종단**: AIVIS 에 검사결과 적재 → MES 폴링 →
  AIVIS KPI `mes_consumed_count` = 적재 건수, 연계율 100% → 재확인이 MES 실적까지 바뀐다.
  로그인은 AIVIS 토큰 하나.
- `services/api/tests/test_mes_link.py` — AIVIS 쪽 보강(개수 저장·스테이징 값·재확인 되돌림·
  워치독 기동·KPI 부분 갱신·모드 보존·REST 토큰).
- 대시보드 `src/pages/Mes*.test.tsx`, `src/api/mes.test.ts`.
