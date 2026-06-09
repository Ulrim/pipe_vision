# AIVIS 백엔드 API (API.md)

> CLAUDE.md §7.4 엔드포인트 요약의 구현. 구현 위치: `services/api/routers/`, `services/api/ws/`.
> 공용 스키마는 `packages/shared-types`(Python `aivis_types` + TS mirror). 변경은 오케스트레이터 승인.

## 인증 / RBAC (§5 M14)
- JWT(HS256, `JWT_SECRET`). 역할 위계: `admin` > `quality` > `operator`.
- `require_role(*roles)` 정확 매칭, `require_min_role(min)` 위계 이상 허용, `require_internal` 검사워커 내부 토큰.

### 역할 정책 요약
| 역할 | 조회(GET inspection/master/kpi/logs) | 재확인(PATCH review) | 기준정보 수정 / KPI 수기입력 | 로그 조회 | 사용자 관리 | 품목 삭제 |
|---|---|---|---|---|---|---|
| operator | O | O | X | X(quality+) | X | X |
| quality | O | O | O | O | X | X |
| admin | O | O | O | O | O | O |

- `/logs` 조회는 quality+ (운영 민감 정보). 그 외 조회(inspection/master/kpi summary·report 중 summary)는 operator+.
- `/kpi/report` 는 quality+ (리포트 산출물).

### 로그 적재 커버리지 (M15, sys_log.category)
| 동작 | category | 위치 |
|---|---|---|
| 로그인 | user | `auth.login` |
| 사용자 생성 | user | `auth.create_user` |
| 기준정보 생성/수정/삭제 | user | `master.*` |
| 검사 재확인 | user | `inspection.review` |
| 검사결과 저장 성공 | db | `inspection._save_with_backup` |
| 검사결과 저장 실패(로컬 큐 백업) | error | `inspection._save_with_backup` |
| MES REST 스테이징 | mes | `mes.quality` |

| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/auth/login` | 공개 | JSON 로그인 → `TokenResponse` |
| POST | `/auth/login/oauth` | 공개 | OAuth2 password-form (Swagger Authorize) |
| POST | `/auth/users` | admin | 사용자 등록 → `UserPublic` |
| GET | `/auth/me` | 로그인 | 내 정보 |

## 검사결과 (§5 M7,M8,M10)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/inspection` | 내부(검사워커) | 결과 적재. 성공 `status=stored`, DB 실패 시 로컬 큐 백업 `status=queued`. 저장 성공 시 WS 푸시 + 연속 NG 알람(M6) |
| POST | `/inspection/retry-queue` | quality+ | 로컬 큐 백업분 재저장 |
| GET | `/inspection?lot=&item=&from=&to=&verdict=&limit=&offset=` | operator+ | 필터 조회(서버 페이지네이션) |
| GET | `/inspection/{id}` | operator+ | 단건 조회 |
| GET | `/inspection/{id}/images` | operator+ | 원본/결과 이미지 경로 → `InspectionImages` |
| GET | `/inspection/{id}/images/{kind}` | operator+ | 원본/결과 이미지 **바이트** 스트리밍(`kind`=raw\|result, `image/jpeg`). 공유 볼륨 `AIVIS_IMAGES_DIR` 하위 상대경로를 traversal 안전하게 서빙. 경로 없음/파일 부재/escape 시 404 |
| PATCH | `/inspection/{id}/review` | operator+ | NG 재확인 결과 입력(`manual_verdict`, `review_flag` 해제) |

### 내부 호출 인증 — POST /inspection (M14)
검사워커(vision 컨테이너) 전용 내부 엔드포인트. `require_internal` 가드:
- `AIVIS_SERVICE_TOKEN` **미설정(기본)**: 사내 단일 호스트 토폴로지(§4)에서 무인증 허용(화이트리스트).
- **설정 시**: `X-Service-Token: <token>` 헤더 또는 `Authorization: Bearer <token>` 가 일치해야 허용, 불일치 시 401.
- 테스트는 `core.security.get_settings` 의존성 오버라이드(monkeypatch)로 토큰 유무를 전환한다.

### 연속 NG 알람 (M6)
`POST /inspection` 처리 시 `cam_id` 단위 **연속 NG 카운터**(`ws/alarm.py`, 인메모리)를 유지한다.
- 매 NG 마다 단일 알람 `{event:"alarm", data:{kind:"ng", id, lot, cam_id, defect_codes}}` 발행.
- 연속 NG 가 임계(`AIVIS_CONSEC_NG_THRESHOLD`, 기본 3) **이상**이면 추가 알람
  `{event:"alarm", data:{kind:"consecutive_ng", cam_id, count, threshold}}` 발행.
- OK 수신 시 해당 cam 카운터 0 리셋.

## 기준정보 (§5 M13)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/master/items` | operator+ | 목록 |
| GET | `/master/items/{code}` | operator+ | 단건 |
| POST | `/master/items` | quality+ | 등록(version=1) |
| PUT | `/master/items/{code}` | quality+ | 부분 갱신(version +1, updated_by/at 기록) |
| DELETE | `/master/items/{code}` | admin | 삭제 |

## KPI (§5 M12, §1.1 — 산출식 그대로)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/kpi/summary?period=YYYY-MM` | operator+ | 월별 자동 산출 → `KpiSummary` |
| POST | `/kpi/manual` | quality+ | 작업공수/리드타임/Claim upsert |
| GET | `/kpi/report?period=&fmt=pdf\|xlsx` | quality+ | 월간 리포트 **파일** 생성(PDF=reportlab / XLSX=openpyxl). `attachment` 다운로드. `period` 미지정 시 당월 |

### 월간 품질 리포트 내용 (M12, GET /kpi/report)
- §1.1 KPI: 공정불량률(ppm) / 검사불량률(%) / 자동검사율(%) / 저장·MES 연계율(%) / 평균 처리속도(ms) / 총·불량 수량.
- 불량유형별 집계(`defect_codes` 배열 카운트, §7.2 코드).
- 일자별 검사수/불량수 표.
- 응답: `Content-Type` = `application/pdf` 또는 xlsx MIME, `Content-Disposition: attachment; filename="aivis_kpi_YYYY-MM.{ext}"`.
- 한글 라벨 기본(PDF 는 reportlab 내장 CID 한글폰트 `HYSMyeongJo-Medium`). 폰트 미가용 시 라틴 라벨 + 코드 폴백.
- 산출 경로: `kpi.py::_compute_summary` 를 summary 엔드포인트와 공유(산출식 일관성). 렌더러는 `core/report.py`.

산출식(§1.1):
- 공정불량률(ppm) = (final_verdict=NG 수 ÷ 총 검사수) × 1,000,000
- 검사불량률(%) = (오검 + 미검) ÷ 총 검사수 × 100
  - 오검 = manual_verdict 입력됨 AND ≠ final_verdict
  - 미검 = review_flag=true AND manual_verdict 미입력
- 자동검사율(%) = final_verdict 존재 수 ÷ 총 검사대상 × 100
- 저장&MES 연계율(%) = mes_synced 수 ÷ 전체 검사 × 100
- avg_proc_time_ms = 평균 처리속도(목표 ≤ 300ms/ea)

## 로그 / MES / 실시간
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/logs?category=&limit=&offset=` | quality+ | 로그 조회(inspect/db/mes/error/user) |
| POST | `/mes/quality` | 내부 | REST 모드 MES 연계 수신(멱등키 중복 방지) |
| WS | `/ws/live` | 공개 | 검사결과/알람 실시간 푸시. 이벤트 봉투 `{event, data}` (event=inspection\|alarm; alarm.data.kind = ng\|consecutive_ng) |
| GET | `/health` | 공개 | 헬스체크(DB 연결 확인) |

## 배포 환경변수 (클라우드 데모)
- `ALLOWED_ORIGINS`: CORS 허용 출처 콤마 목록(예 `https://aivis-hmi.vercel.app,https://aivis-dashboard.vercel.app`). 미설정 시 `*`(모든 출처, credentials 불가). 명시 목록이면 `allow_credentials=True`.
- `AIVIS_SEED_DEMO_ITEM`(기본 `false`): 데모 배포에서 `true`로 켜면 `item_master`에 데모 품목 1건을 멱등 시드(워커 검사결과 FK 충족).
- `AIVIS_DEMO_ITEM_CODE`(기본 `HP12`): 데모 시드 품목코드.

## 공용 스키마 (packages/shared-types)
`InspectionResult`, `ItemMaster(+Create/Update)`, `ReviewUpdate`, `InspectionImages`,
`LengthResult`, `SurfaceResult`, `VerdictResult`(비전 파이프라인),
`KpiSummary`, `KpiManual`, `UserCreate/Public`, `LoginRequest`, `TokenResponse`, `SysLog`,
Enum: `DefectCode`, `Verdict`, `Role`, `LogCategory`, `CameraView`.
Python(`aivis_types`)과 TS(`ts/src/index.ts`)의 필드명/타입은 1:1 일치(자동 검증).
