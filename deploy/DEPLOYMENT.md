# AIVIS 클라우드 데모 배포 가이드

이 문서는 AIVIS를 **무료/저비용 클라우드 데모**로 띄우는 따라하기 가이드다. 현장 단일
호스트(`docker compose up`)와 별개로, 인터넷에서 접근 가능한 데모를 만든다.

- 소유: devops. 이 문서/`render.yaml`/`apps/*/vercel.json`/`.env.example`만 배포 메타다.
- 애플리케이션 소스(`services/*`, `apps/*/src`, `packages/*`)는 변경하지 않는다.

---

## 0) 아키텍처 한 장 (무엇이 어디로)

```
            ┌────────────────────── Vercel (정적 프론트엔드) ──────────────────────┐
            │  apps/hmi  (작업자 HMI)            apps/dashboard (관리자 대시보드)     │
            │  VITE_API_BASE, VITE_WS_URL        VITE_API_BASE                       │
            └───────────────┬───────────────────────────┬──────────────────────────┘
                            │ https REST / wss WebSocket │ https REST (폴링)
                            ▼                            ▼
            ┌──────────────────────── Render (Docker) ─────────────────────────────┐
            │  aivis-api (web, :8000)  ── /health, REST, /ws/live, CORS              │
            │      ▲ POST /inspection (X-Service-Token)                             │
            │  aivis-vision (worker)   ── sim 카메라 → 검사 → api 로 결과 POST        │
            │  aivis-mes-watchdog (worker, 선택) ── mes_synced=false 재연계          │
            └───────────────┬───────────────────────────────────────────────────────┘
                            │ postgresql+psycopg (sslmode=require, Session 풀러)
                            ▼
                   ┌───────────────────────┐
                   │  Supabase PostgreSQL  │  (외부, 무료 티어 · Supavisor 풀러)
                   └───────────────────────┘
```

- **프론트엔드**: Vercel 프로젝트 2개(hmi, dashboard). 정적 SPA, 히스토리 폴백.
- **백엔드**: Render Blueprint(`render.yaml`)로 api(web) + vision(worker) + (선택)
  mes-watchdog(worker)를 한 번에 배포.
- **DB**: Supabase PostgreSQL(외부). `DATABASE_URL`을 Render에 수동 입력한다(Render가
  Supabase를 프로비저닝하지 않으므로 `sync:false`). **반드시 Supavisor "Session 풀러"
  연결을 쓴다**(아래 1단계 — Render는 IPv4라 Supabase Direct(IPv6) 연결이 안 됨).
- **이미지 스토리지는 클라우드에서 Supabase Storage 를 사용한다(필수).** 로컬 단일 호스트
  compose 는 api·vision 이 같은 `images` 볼륨을 공유하지만, 클라우드(Render)는 api·vision 이
  **분리된 컨테이너**라 로컬 디스크를 공유할 수 없다. 따라서 객체 스토리지가 필요하며,
  **워커가 검사 이미지를 업로드**하고 **api 가 프록시 서빙**한다. api·vision 양쪽에
  `AIVIS_STORAGE_BACKEND=supabase` + `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` +
  `SUPABASE_STORAGE_BUCKET`(기본 `inspection-images`)를 **동일하게** 설정한다(아래 1-B 단계).

> 코드 근거: api Dockerfile `uvicorn main:app` :8000 + `/health`(services/api/main.py),
> 운영(postgres)은 lifespan이 테이블을 자동 생성하지 않음 → `alembic upgrade head` 필수.
> 워커 `python -m worker`(services/vision), heartbeat `/tmp/vision_ready`.

---

## 1) Supabase PostgreSQL

1. https://supabase.com 가입 → **New project** 생성. **Database Password**를 강하게 설정하고
   따로 보관한다(이 비번이 `DATABASE_URL`에 들어간다 — API 키와 다름). Region은 Render와
   가까운 곳 권장.
2. **연결 문자열은 반드시 "Session 풀러"(Supavisor)를 쓴다.** 프로젝트 상단 **Connect** 버튼
   (또는 **Project Settings → Database → Connection string**) → **Session pooler** 탭에서
   복사한다. 형식은 대략 아래와 같다(포트 **5432**, 호스트가 `…pooler.supabase.com`):

   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

   > **왜 Direct가 아니라 Session 풀러인가**: Supabase의 Direct 연결
   > (`db.<ref>.supabase.co:5432`)은 **IPv6 전용**이라, 아웃바운드가 IPv4인 Render에서
   > 연결되지 않는다. Supavisor 풀러는 **IPv4**를 제공한다. 그중 **Session 모드(5432)** 는
   > prepared statement를 지원해 영속 컨테이너(Render api/worker)와 Alembic 마이그레이션에
   > 모두 안전하다. **Transaction 풀러(6543)는 쓰지 마라** — prepared statement가 비활성이라
   > psycopg에서 별도 설정이 필요하다.

3. **드라이버 접두사를 `postgresql+psycopg://` 로 교체**하고 `?sslmode=require` 를 붙인다
   (api는 psycopg v3를 쓰고 Supabase는 SSL 필수). 비밀번호에 특수문자(`@ : / ?` 등)가 있으면
   **URL 인코딩**한다. 최종 형태:

   ```
   postgresql+psycopg://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   ```

   - 데이터베이스 이름은 Supabase 기본값 `postgres` 다.
   - 우리 테이블은 `public` 스키마에 생성된다(Supabase의 `auth`/`storage` 스키마와 무관).
4. 이 문자열을 다음 단계에서 Render의 `DATABASE_URL`(api, mes-watchdog)에 **동일하게** 입력한다.

---

## 1-B) Supabase Storage (검사 이미지 객체 스토리지)

클라우드는 api·vision 이 분리 컨테이너라 로컬 디스크를 공유할 수 없다 → **Supabase Storage
가 필수**다(워커 업로드 / api 프록시 서빙). 같은 Supabase 프로젝트에서 설정한다.

1. Supabase 대시보드 → **Storage** → **New bucket** → 이름 **`inspection-images`** 로
   생성하고 **Private**(공개 체크 해제)로 둔다. 서빙은 api 가 service_role 키로 프록시하므로
   버킷을 public 으로 만들 필요가 없다.
   - 버킷 이름을 바꾸면 api·vision 의 `SUPABASE_STORAGE_BUCKET` 도 같은 값으로 바꾼다.
2. **service_role 키**를 확보한다: **Project Settings → API → Project API keys** 의
   **`service_role`**(secret) 값을 복사한다. 같은 화면 상단의 **Project URL**
   (`https://<project-ref>.supabase.co`)이 `SUPABASE_URL` 이다(끝 슬래시 없음).
3. 2단계에서 만들 Render env 에 다음을 api·vision **양쪽 동일하게** 입력한다:
   - `AIVIS_STORAGE_BACKEND=supabase`(render.yaml 고정값)
   - `SUPABASE_URL=https://<project-ref>.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY=<service_role secret>`
   - `SUPABASE_STORAGE_BUCKET=inspection-images`(render.yaml 고정값)

> ⚠️ **보안 경고**: `service_role` 키는 RLS 를 우회하는 **관리자급 비밀키**다. **절대
> 프론트엔드/클라이언트(Vercel `VITE_*`, 브라우저)에 노출하지 마라.** Render의 서버 측 env
> (api·vision)에만 `sync:false` 비밀값으로 보관한다. 프론트가 직접 Supabase 에 접근하지 않고
> 반드시 api 프록시(`GET /inspection/{id}/images`)를 거치게 한다. 익명 공개키(anon)와 혼동
> 금지 — 업로드/서빙에는 service_role 을 쓴다.

---

## 2) Render (api + vision worker [+ mes-watchdog])

루트의 `render.yaml` 블루프린트로 백엔드 일체를 배포한다.

1. https://render.com 가입 → **New + → Blueprint** → 이 저장소를 연결 → `render.yaml` 감지.
2. Render가 다음을 생성한다:
   - `aivis-api` (web, Docker, `services/api/Dockerfile`, 루트 컨텍스트, `/health`, :8000)
   - `aivis-vision` (worker, Docker, `services/vision/Dockerfile`, 루트 컨텍스트)
   - `aivis-mes-watchdog` (worker, `python -m mes.cli watchdog`) — 데모 연계율 표시용(선택).
   - env group `aivis-shared` (JWT_SECRET/AIVIS_SERVICE_TOKEN 자동생성, 시드 admin).
3. **환경변수 입력표** — Blueprint가 `sync:false`로 표시한 값들을 입력한다:

   | 서비스 | 키 | 예시 / 출처 | 설명 |
   |---|---|---|---|
   | api | `DATABASE_URL` | `postgresql+psycopg://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require` | 1)에서 만든 Supabase **Session 풀러** URL |
   | api | `ALLOWED_ORIGINS` | (3단계 후 입력) | Vercel 두 도메인 콤마목록. CORS |
   | api | `SUPABASE_URL` | `https://<ref>.supabase.co` | 1-B) 프로젝트 URL(끝 슬래시 없음) |
   | api | `SUPABASE_SERVICE_ROLE_KEY` | service_role secret | 1-B) **비밀**. 프론트 노출 금지 |
   | vision | `SUPABASE_URL` | api와 **동일** | 워커 업로드용. 같은 값 |
   | vision | `SUPABASE_SERVICE_ROLE_KEY` | api와 **동일** | 워커 업로드용. 같은 **비밀**값 |
   | aivis-shared | `JWT_SECRET` | 자동생성(generateValue) | JWT 서명키. api/worker 공유 |
   | aivis-shared | `AIVIS_SERVICE_TOKEN` | 자동생성 | 워커→api `X-Service-Token`. 동일값 |
   | aivis-shared | `AIVIS_SEED_ADMIN_USER` | `admin` | 시드 관리자 계정 |
   | aivis-shared | `AIVIS_SEED_ADMIN_PASSWORD` | 강한 임의값 | 시드 관리자 비번(데모용, 교체 권장) |
   | mes-watchdog | `DATABASE_URL` | api와 동일 Supabase URL | 워치독도 같은 DB |

   Blueprint가 고정값으로 넣는 키(확인용, 입력 불필요):
   - api: `AIVIS_SEED_ON_STARTUP=true`, `AIVIS_SEED_DEMO_ITEM=true`,
     `AIVIS_DEMO_ITEM_CODE=HP12`, `MES_MODE=table`, `LOG_LEVEL=INFO`,
     `AIVIS_STORAGE_BACKEND=supabase`, `SUPABASE_STORAGE_BUCKET=inspection-images`.
   - vision: `AIVIS_CAMERA=sim`, `AIVIS_API_URL=http://aivis-api:8000`,
     `AIVIS_ITEM_CODE=HP12`, `AIVIS_WORKER_INTERVAL_MS=1500`,
     `AIVIS_STORAGE_BACKEND=supabase`, `SUPABASE_STORAGE_BUCKET=inspection-images`.
     (`AIVIS_DATASET_DIR` 미설정 → 워커가 합성 이미지를 자동 생성)
   - mes-watchdog: `MES_MODE=table`, `MES_WATCHDOG_INTERVAL_S=10`.

4. **마이그레이션**: api 서비스는 `preDeployCommand: alembic upgrade head` 가 매 배포마다
   라이브 전환 직전에 실행된다(이미지 WORKDIR `/app` = services/api 이므로 `alembic.ini` +
   `alembic/` 가 그 자리에 있다). 운영 postgres는 테이블을 자동 생성하지 않으므로 이 단계가
   **반드시** 성공해야 한다. 첫 배포 로그에서 `alembic upgrade head` 가 OK인지 확인한다.
   - preDeploy 단계가 실패하면 배포가 중단된다 → `DATABASE_URL`/Supabase 접근부터 점검.
     특히 **Session 풀러(5432, IPv4)** 가 아니면 Render에서 연결 자체가 안 될 수 있다.

5. **검증**:
   - `GET https://aivis-api.onrender.com/health` → `{"status":"ok", ...}` (콜드스타트 시
     30~60초 소요 가능).
   - 시드 admin 로그인: `POST /auth/login` (user=`admin`, pw=입력한 값) → 토큰 수신.
   - `aivis-vision` 로그에서 검사 결과 POST가 **HTTP 201** 로 찍히는지 확인(워커가 api ready
     대기 후 HP12 item_master 를 찾고 루프를 돈다).
   - (선택) `aivis-mes-watchdog` 로그에서 미전송 행 재연계 주기 동작 확인.

> Render WebSocket: web 서비스는 WS를 지원하므로 `/ws/live` 가 동작한다(HMI 실시간).
> Render free web 서비스는 ~15분 유휴 후 **슬립**하고 다음 요청에서 콜드스타트한다(데모
> 첫 접속이 느릴 수 있음). 안정적 데모는 api를 paid starter로 올리거나 콜드스타트를 감수한다.

---

## 3) Vercel (hmi + dashboard)

프론트엔드는 **Vercel 프로젝트 2개**를 만든다(모노레포에서 각 앱을 Root Directory로 지정).
`@aivis/shared-types` 워크스페이스 해석이 깨지지 않도록 **설치/빌드는 루트 워크스페이스에서**
수행한다. 각 앱의 `vercel.json` 이 이를 이미 명시한다.

### apps/hmi 프로젝트

| 설정 | 값 |
|---|---|
| Root Directory | `apps/hmi` |
| Framework Preset | Vite |
| Install Command | `cd ../.. && npm install` (vercel.json에 명시됨) |
| Build Command | `cd ../.. && npm run build:hmi` (vercel.json에 명시됨) |
| Output Directory | `dist` (vercel.json에 명시됨) |

환경변수(Production):

| 키 | 값 | 비고 |
|---|---|---|
| `VITE_API_BASE` | `https://aivis-api.onrender.com` | **베이스 URL**(끝 슬래시 없음). REST 호출에 사용 |
| `VITE_WS_URL` | `wss://aivis-api.onrender.com/ws/live` | **`/ws/live` 경로 포함 전체 URL**. wss 필수 |

> 코드 근거(apps/hmi/src/lib/config.ts): `VITE_API_BASE` 는 베이스 URL이며 끝 슬래시를
> 제거한다. `VITE_WS_URL` 이 설정되면 **그 값을 그대로** WS 주소로 쓴다(자동 변환 없음) →
> 반드시 `wss://…/ws/live` 형태여야 한다. 미설정 시 코드가 `VITE_API_BASE` 의 http→ws
> 변환 + `/ws/live` 를 자동 생성하지만, 명시 입력을 권장한다.

### apps/dashboard 프로젝트

| 설정 | 값 |
|---|---|
| Root Directory | `apps/dashboard` |
| Framework Preset | Vite |
| Install Command | `cd ../.. && npm install` (vercel.json에 명시됨) |
| Build Command | `cd ../.. && npm run build:dashboard` (vercel.json에 명시됨) |
| Output Directory | `dist` (vercel.json에 명시됨) |

환경변수(Production):

| 키 | 값 | 비고 |
|---|---|---|
| `VITE_API_BASE` | `https://aivis-api.onrender.com` | 베이스 URL. 대시보드는 **REST 폴링만** 사용(WS 미사용 → VITE_WS_URL 불필요) |

> 코드 근거(apps/dashboard/src/lib/env.ts): 대시보드는 `VITE_API_BASE` 만 읽는다.
> 끝 슬래시를 제거하며, 미설정 시 same-origin(빈 문자열)으로 폴백한다.

두 `vercel.json` 은 SPA 히스토리 폴백 rewrite(`/(.*) → /index.html`), `framework: vite`,
`outputDirectory: dist` 를 포함한다. Vercel 대시보드에서 위 값들이 자동 채워지지 않으면
표대로 수동 입력한다.

배포 후 각 앱의 Vercel 도메인을 기록한다(예: `https://aivis-hmi.vercel.app`,
`https://aivis-dashboard.vercel.app`).

---

## 4) 연결 마무리 (CORS)

1. 3)에서 얻은 **Vercel 두 도메인**을 Render `aivis-api` 의 `ALLOWED_ORIGINS` 에 콤마목록으로
   입력한다(끝 슬래시 없음):

   ```
   ALLOWED_ORIGINS=https://aivis-hmi.vercel.app,https://aivis-dashboard.vercel.app
   ```

2. api 서비스를 **재배포(Manual Deploy)** 한다 → CORS 미들웨어에 도메인이 반영된다.
   (`ALLOWED_ORIGINS` 미설정이면 코드가 `*` 로 폴백하지만, 자격증명/실서비스에는 명시 권장.)
3. **스모크 테스트**:
   - 대시보드: KPI 카드 값과 LOT 이력 목록이 보인다(워커가 적재 중).
   - HMI: 실시간 검사 카드가 흐른다(WS 연결). NG 발생 시 알람 표시.
   - 로그인(admin) 후 HMI에서 NG 재확인(manual verdict) 입력이 저장된다.
   - (선택) 대시보드 MES 연계율이 watchdog 동작으로 100%에 수렴한다.

---

## 4-B) 로그인 / 사용자 관리 (사내 도구 — 전체 로그인)

이 데모는 **사내 도구**로 운영하며 **공개 회원가입(self sign-up)이 없다.** HMI·대시보드 모두
**로그인이 필요**하며(익명 접근 불가), 계정은 **관리자(admin)** 가 생성한다.

1. **시드 admin 으로 최초 로그인**: api 가 첫 기동 시 `AIVIS_SEED_ADMIN_USER`(=`admin`) /
   `AIVIS_SEED_ADMIN_PASSWORD` 로 관리자 계정을 시드한다. 이 계정으로 로그인해 토큰을 받는다.

   ```bash
   curl -s -X POST https://aivis-api.onrender.com/auth/login \
     -H 'content-type: application/json' \
     -d '{"username":"admin","password":"<시드 비번>"}'
   # → {"access_token":"<JWT>", ...}
   ```

2. **사용자 생성**(admin 토큰 필요): 작업자/품질/관리자 계정을 `POST /auth/users` 로 만든다.
   `role` 은 `operator` | `quality` | `admin` 중 하나(CLAUDE.md §7.1).

   ```bash
   curl -s -X POST https://aivis-api.onrender.com/auth/users \
     -H "authorization: Bearer <admin JWT>" \
     -H 'content-type: application/json' \
     -d '{"username":"line1","password":"<강한 비번>","role":"operator"}'
   ```

3. 발급한 계정/비번을 현장 사용자에게 배포한다. HMI·대시보드 접속 시 각자 로그인한다
   (토큰은 브라우저에 보관, WS 는 `?token=` 으로 인증 — 트러블슈팅 참조).

> 공개 가입 엔드포인트가 없으므로, 사용자 추가는 **항상 admin 의 `POST /auth/users`** 를
> 거친다. 운영 전 시드 admin 비번 교체는 아래 보안 체크리스트(필수)를 따른다.

---

## 4-C) 이미지 동작 확인 스모크 (Supabase Storage)

이미지 파이프라인이 끝까지 동작하는지 확인한다(워커 업로드 → 버킷 객체 생성 → 프록시 서빙).

1. **워커 업로드**: `aivis-vision` 로그에 검사 결과 POST(201)가 찍히면, 같은 검사에 대해
   워커가 `raw/…jpg` · `result/…jpg` 객체를 Supabase Storage 에 업로드한다.
2. **버킷 객체 생성 확인**: Supabase 대시보드 → **Storage → `inspection-images`** 에
   `raw/` · `result/` 키로 객체가 쌓이는지 본다(업로드 실패는 검사 적재를 막지 않으므로,
   객체가 안 보이면 키/URL/백엔드를 점검 — 트러블슈팅).
3. **api 프록시 서빙 확인**: api 는 service_role 키로 객체를 받아 프록시 서빙한다. 로그인
   토큰으로 검사 1건의 이미지를 요청해 200 + `image/jpeg` 인지 확인한다.

   ```bash
   # 최근 검사 id 확인 후 (kind = raw | result, 경로 파라미터)
   curl -s -D- -o /dev/null \
     -H "authorization: Bearer <JWT>" \
     "https://aivis-api.onrender.com/inspection/<id>/images/result"
   # → HTTP/1.1 200, content-type: image/jpeg
   ```

4. **프론트 표시 확인**: 대시보드 이미지 이력 / HMI 실시간 카드에서 원본·결과 이미지가
   보이면 끝단까지 정상이다. 404 면 트러블슈팅의 "이미지 404" 행을 따른다.

---

## 4-D) 보안 하드닝 체크리스트 (웹 오픈 전 **필수**)

인터넷에 노출하기 전에 아래를 **모두** 완료한다. 데모 기본값/폴백은 운영에 부적합하다.

- [ ] **`JWT_SECRET`** — 강한 임의값(예: `openssl rand -hex 32`). `aivis-shared` 그룹의
      `generateValue` 가 자동 생성하지만, 직접 회전했다면 api·worker 가 **같은 값**인지 확인.
- [ ] **`AIVIS_SERVICE_TOKEN`** — 강한 임의값. 워커→api `X-Service-Token` 용. api·vision 이
      **동일 값**(`aivis-shared` 그룹)이어야 워커 POST 가 401/403 없이 통과.
- [ ] **시드 admin 비번 교체** — `AIVIS_SEED_ADMIN_PASSWORD` 를 강한 값으로 두고, 오픈 후
      `POST /auth/users`(또는 비번 변경)로 운영 관리자 계정을 따로 만들고 시드 계정은 정리.
- [ ] **`ALLOWED_ORIGINS`** — 실제 Vercel 도메인 콤마목록으로 명시(끝 슬래시 없음).
      **`*` 폴백 금지** (사내 도구이므로 출처를 좁힌다).
- [ ] **`SUPABASE_SERVICE_ROLE_KEY`** — Render 서버 측 env(api·vision)에만 `sync:false`
      비밀로 보관. **프론트(Vercel `VITE_*`)·브라우저·리포지토리에 절대 노출 금지.**
- [ ] **`DATABASE_URL`** — Supabase 비번 포함 비밀값. `sync:false` 로만 관리, 로그/문서에
      평문 노출 금지. Session 풀러(5432, IPv4) URL 인지 재확인.
- [ ] **공개 가입 없음 재확인** — 사용자 추가는 admin `POST /auth/users` 로만. HMI·대시보드가
      비로그인 상태로 데이터를 노출하지 않는지 확인.

---

## 5) 비용 / 한계 / 보안

- **Render free 슬립·콜드스타트**: api web이 유휴 시 슬립 → 첫 접속 30~60초 지연. worker도
  free 티어 한도(시간/리소스) 영향. 안정 데모는 starter 유료 권장.
- **Supabase free 한도**: 무료 프로젝트는 **약 1주간 비활성 시 자동 일시정지(pause)** 되며,
  재개하려면 대시보드에서 수동 restore가 필요하다(데모를 오래 방치하면 DB가 멈춤). 스토리지/
  대역폭 한도도 있다. 상시 데모는 유료 플랜 또는 주기적 접속으로 paused를 피한다.
- **스코프 외(CLAUDE.md §2)**: 실카메라/조명/트리거 HW, MES 본체는 데모에 포함하지 않는다.
  데모는 `AIVIS_CAMERA=sim` 합성 데이터 + `MES_MODE=table`(내부 스테이징) 로 동작한다.
- **이미지 스토리지**: 클라우드는 **Supabase Storage** 를 사용한다(분리 컨테이너라 로컬 디스크
  공유 불가 → 필수). 워커가 업로드하고 api 가 프록시 서빙한다(1-B / 4-C 단계). Supabase 무료
  티어의 스토리지/대역폭 한도가 적용된다.
- **보안 경고(웹 오픈 전 반드시)**: 4-D) **보안 하드닝 체크리스트**를 모두 완료한다
  (`JWT_SECRET`/`AIVIS_SERVICE_TOKEN` 강한 임의값, 시드 admin 비번 교체, `ALLOWED_ORIGINS`
  명시 도메인(`*` 금지), `SUPABASE_SERVICE_ROLE_KEY`·`DATABASE_URL` 비밀 관리).

---

## 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| 브라우저 콘솔 CORS 차단 | `ALLOWED_ORIGINS` 에 Vercel 도메인 누락/슬래시 포함 | 4)대로 정확히 입력 후 api 재배포 |
| WS 연결 실패 / mixed content | `VITE_WS_URL` 이 `ws://` 이거나 `/ws/live` 누락 | `wss://aivis-api.onrender.com/ws/live` 로 설정 후 재배포 |
| WS 연결이 곧바로 끊김(1008/4401) | `/ws/live` 에 `?token=` 누락 또는 토큰 만료 | HMI 에서 로그인 후 유효 토큰으로 `wss://…/ws/live?token=<JWT>` 접속(만료 시 재로그인) |
| 이미지 404 / 깨짐(대시보드·HMI) | `AIVIS_STORAGE_BACKEND`/버킷명/`SUPABASE_*` 키 불일치 또는 객체 미업로드 | api·vision 양쪽 `AIVIS_STORAGE_BACKEND=supabase` + 동일 `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`/`SUPABASE_STORAGE_BUCKET` 확인, Storage 버킷에 객체 생성 여부 점검(4-C) |
| 첫 접속 매우 느림 | Render free 슬립 콜드스타트 / Supabase 깨우기 | 정상. 재시도 또는 starter 업그레이드 |
| DB 연결 타임아웃/거부 | Supabase **Direct(IPv6)** URL 사용 또는 Transaction 풀러(6543) | **Session 풀러(5432, `…pooler.supabase.com`)** URL로 교체 |
| `prepared statement` 관련 오류 | Transaction 풀러(6543) 사용 | Session 풀러(5432)로 교체 |
| api 5xx, 테이블 없음 | 마이그레이션 미실행 | api 배포 로그의 `alembic upgrade head` 확인, 실패 시 `DATABASE_URL` 점검 |
| 워커 로그에 item 못 찾음 / FK 오류 | HP12 item_master 부재 | api `AIVIS_SEED_DEMO_ITEM=true` 확인(워커 `AIVIS_ITEM_CODE`와 일치) |
| 워커가 api에 접속 못함 | `AIVIS_API_URL` 스킴 누락 | `http://aivis-api:8000`(스킴 포함) 인지 확인 |
| 워커 POST 401/403 | `AIVIS_SERVICE_TOKEN` 불일치 | api/worker가 같은 env group 토큰을 쓰는지 확인 |
| 대시보드 데이터 없음 | 워커 미동작 또는 DB 분리 | 워커 로그 201 여부 + api/워커/워치독 동일 `DATABASE_URL` 확인 |
| MES 연계율 < 100% | watchdog 미배포/중단 | `aivis-mes-watchdog` 가동 또는 주기 단축(`MES_WATCHDOG_INTERVAL_S`) |
