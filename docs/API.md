# AIVIS 백엔드 API (API.md)

> CLAUDE.md §7.4 엔드포인트 요약의 구현. 구현 위치: `services/api/routers/`, `services/api/ws/`.
> 공용 스키마는 `packages/shared-types`(Python `aivis_types` + TS mirror). 변경은 오케스트레이터 승인.

## 인증 / RBAC (§5 M14)
- JWT(HS256, `JWT_SECRET`). 역할 위계: `admin` > `quality` > `operator`.
- `require_role(*roles)` 정확 매칭, `require_min_role(min)` 위계 이상 허용.

| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/auth/login` | 공개 | JSON 로그인 → `TokenResponse` |
| POST | `/auth/login/oauth` | 공개 | OAuth2 password-form (Swagger Authorize) |
| POST | `/auth/users` | admin | 사용자 등록 → `UserPublic` |
| GET | `/auth/me` | 로그인 | 내 정보 |

## 검사결과 (§5 M7,M8,M10)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/inspection` | 내부(검사워커) | 결과 적재. 성공 `status=stored`, DB 실패 시 로컬 큐 백업 `status=queued`. 저장 성공 시 WS 푸시 |
| POST | `/inspection/retry-queue` | quality+ | 로컬 큐 백업분 재저장 |
| GET | `/inspection?lot=&item=&from=&to=&verdict=&limit=&offset=` | 공개* | 필터 조회(서버 페이지네이션) |
| GET | `/inspection/{id}` | 공개* | 단건 조회 |
| GET | `/inspection/{id}/images` | 공개* | 원본/결과 이미지 경로 → `InspectionImages` |
| PATCH | `/inspection/{id}/review` | operator+ | NG 재확인 결과 입력(`manual_verdict`, `review_flag` 해제) |

(*) 조회는 현재 미인증 허용. 운영 정책 확정 시 `require_min_role` 부착 예정(오케스트레이터 결정).

## 기준정보 (§5 M13)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/master/items` | 공개 | 목록 |
| GET | `/master/items/{code}` | 공개 | 단건 |
| POST | `/master/items` | quality+ | 등록(version=1) |
| PUT | `/master/items/{code}` | quality+ | 부분 갱신(version +1, updated_by/at 기록) |
| DELETE | `/master/items/{code}` | admin | 삭제 |

## KPI (§5 M12, §1.1 — 산출식 그대로)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/kpi/summary?period=YYYY-MM` | 공개 | 월별 자동 산출 → `KpiSummary` |
| POST | `/kpi/manual` | quality+ | 작업공수/리드타임/Claim upsert |
| GET | `/kpi/report?period=&fmt=pdf\|xlsx` | quality+ | 월간 리포트(현재 JSON stub, P4 에서 PDF/XLSX) |

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
| WS | `/ws/live` | 공개 | 검사결과/알람 실시간 푸시. 이벤트 봉투 `{event, data}` (event=inspection\|alarm) |
| GET | `/health` | 공개 | 헬스체크(DB 연결 확인) |

## 공용 스키마 (packages/shared-types)
`InspectionResult`, `ItemMaster(+Create/Update)`, `ReviewUpdate`, `InspectionImages`,
`LengthResult`, `SurfaceResult`, `VerdictResult`(비전 파이프라인),
`KpiSummary`, `KpiManual`, `UserCreate/Public`, `LoginRequest`, `TokenResponse`, `SysLog`,
Enum: `DefectCode`, `Verdict`, `Role`, `LogCategory`, `CameraView`.
Python(`aivis_types`)과 TS(`ts/src/index.ts`)의 필드명/타입은 1:1 일치(자동 검증).
