# AIVIS 클라우드 운영 배포 가이드 (Vercel + Render + Supabase)

> 2026-10-15 개정 — 종전 "클라우드 데모"(클라우드에서 가짜 카메라가 돌던 구성)를 **실제 운영**
> 구성으로 바꿨다. 검사는 현장 라즈베리파이가 하고, 결과·사진은 클라우드로 올라가며, 화면은
> 어디서든 브라우저로 본다. 현장 단독(오프라인) 구성은 `docs/OPERATIONS_PI.md` 를 따른다.

- 소유: devops. 배포 메타: 이 문서, `render.yaml`, `apps/*/vercel.json`, `apps/*/.env.example`,
  `deploy/aivis-worker.env.example`.

---

## 0) 구성 한 장

```
 [브라우저 — 사무실·현장·휴대폰]
     │ https                                   │ https
     ▼                                         ▼
 Vercel: aivis-hmi (작업자 화면)        Vercel: aivis-dashboard (관리자·MES 화면)
     │ REST + wss(/ws/live)                    │ REST              │ REST
     ▼                                         ▼                   ▼
 ┌──────────────── Render (Docker 웹서비스 2개) ─────────────────────────────┐
 │ aivis-api  :PORT  검사결과·기준정보·KPI·권한·사진 프록시·MES 연계 표       │
 │   ▲ POST /inspection · PUT /inspection/images · GET /master (서비스 토큰) │
 │ aivis-mes  :PORT  수주·작업지시·생산실적·출하·클레임 (연계 표 10초 폴링)   │
 └──────────┬───────────────────────────────────┬──────────────────────────┘
            │ Postgres (Session pooler, IPv4)   │ Storage(서버만 키 보유)
            ▼                                   ▼
     Supabase Postgres  ◀── 같은 DB ──  Supabase Storage (inspection-images, private)
            ▲
            │ https (서비스 토큰)
 [현장 라즈베리파이 1·2호기] 카메라 → 판정 → 결과·사진을 aivis-api 로 (끊기면 스풀 후 재전송)
```

- **비밀 키는 서버에만**: Supabase service role key·DB 비밀번호는 Render 에만 둔다. 파이는
  **서비스 토큰 하나**로 결과·사진을 올리고 기준정보를 읽는다(쓰기·관리 권한 없음).
- **화면(Vercel)은 정적 파일**이다. 데이터는 브라우저가 Render API 에서 직접 받는다.
- 한 DB: AIVIS 표와 MES 표(`mes_` 접두어)가 같은 Supabase DB 에 있다. MES 는 AIVIS 가
  쌓는 연계 표(`mes_quality_if`)를 가져가며 `consumed` 로 표시한다(= KPI 'MES 수신 확인').

**순서**: 1) Supabase → 2) Render → 3) Vercel → 4) CORS 마무리 → 5) 파이 연결 → 6) 확인.

---

## 1) Supabase — DB·사진 저장소

1. https://supabase.com → **New project**. **Database Password** 를 강하게 정하고 따로 보관한다.
   Region 은 Render 와 가까운 곳(예: Render Singapore ↔ Supabase Singapore).
2. **DB 주소 — 반드시 "Session pooler"**: 상단 **Connect** → **Session pooler** 탭에서 복사.
   ```
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```
   - 그대로 붙여도 된다(앱·마이그레이션이 `postgresql+psycopg://` 로 바꾼다). 끝에
     `?sslmode=require` 를 붙이기를 권한다. 비밀번호의 `@ : / ?` 등은 URL 인코딩.
   - **Direct 연결(`db.<ref>.supabase.co`)은 IPv6 전용**이라 IPv4 인 Render 에서 안 된다.
     **Transaction pooler(6543)도 쓰지 않는다**(prepared statement 비활성).
3. **사진 버킷**: **Storage → New bucket** → 이름 `inspection-images`, **Private**(공개 해제).
4. **서버용 키**: **Project Settings → API** 의 **Project URL**(`https://<ref>.supabase.co`,
   끝 슬래시 없음)과 **`service_role`**(secret) 키를 복사한다.
   > ⚠️ service_role 은 RLS 를 우회하는 **관리자 키**다. Render 의 api 에만 넣는다. Vercel
   > (`VITE_*`)·파이·저장소 어디에도 두지 않는다.

---

## 2) Render — API · MES

1. https://render.com → **New + → Blueprint** → 이 저장소 연결 → `render.yaml` 감지.
2. 만들어지는 것: 웹서비스 **`aivis-api`**, **`aivis-mes`**, env 그룹 **`aivis-shared`**
   (JWT_SECRET·AIVIS_SERVICE_TOKEN 자동 생성). 가짜 카메라 워커는 없다 — 검사는 파이가 한다.
3. **직접 입력값**(Blueprint 가 물어본다):

   | 서비스 | 키 | 값 |
   |---|---|---|
   | aivis-api | `DATABASE_URL` | 1-2 의 Session pooler 주소 |
   | aivis-api | `AIVIS_SEED_ADMIN_PASSWORD` | 관리자 `admin` 초기 비밀번호(강하게) |
   | aivis-api | `SUPABASE_URL` | 1-4 Project URL |
   | aivis-api | `SUPABASE_SERVICE_ROLE_KEY` | 1-4 service_role |
   | aivis-api | `ALLOWED_ORIGINS` | 지금은 비워 두고 4) 에서 입력 |
   | aivis-mes | `DATABASE_URL` | **api 와 같은** 주소 |
   | aivis-mes | `ALLOWED_ORIGINS` | 지금은 비워 두고 4) 에서 입력 |

4. 배포가 끝나면 두 주소를 기록한다(이름이 겹치면 뒤에 글자가 붙는다 — 화면에 나온 그대로):
   `https://aivis-api.onrender.com`, `https://aivis-mes.onrender.com`.
   - `…/health` 가 둘 다 `{"status":"ok"}` 면 정상. api 는 **기동 때 마이그레이션**을 돈다
     (`AIVIS_MIGRATE_ON_START=true` — 무료 인스턴스에는 pre-deploy 가 없다). 로그에
     `[entrypoint] alembic upgrade head` 가 보인다.
   - mes 의 `/health` 에 `"table_intake": true` 면 연계 표를 읽고 있다.
5. **서비스 토큰 확인**: Render → **Env Groups → aivis-shared → `AIVIS_SERVICE_TOKEN`** 값을
   복사해 둔다(5) 파이 설정에 넣는다).

> **요금**: `plan: free` 는 15분 무요청이면 잠들고(첫 요청 30~60초), 워크스페이스당 **월 750시간**
> 상한이다 — 웹서비스 2개를 24시간 돌리면 한 달을 못 채운다. **생산 라인에 붙이면 aivis-api 는
> Starter 이상**을 권한다. 잠들어도 파이는 결과를 스풀에 보관했다가 깨어나면 보내므로 유실은 없다.

---

## 3) Vercel — 화면 2개

Vercel → **Add New → Project** → 같은 저장소를 **두 번** 가져온다. 설치·빌드는 모노레포 루트에서
돈다(각 앱의 `vercel.json` 에 이미 적혀 있다 — Framework **Vite**, Output `dist`).

| 프로젝트 | Root Directory | 환경변수 (Production) |
|---|---|---|
| **aivis-hmi** (작업자 화면) | `apps/hmi` | `VITE_API_BASE=https://aivis-api.onrender.com`<br>`VITE_WS_URL=wss://aivis-api.onrender.com/ws/live` |
| **aivis-dashboard** (관리자·MES) | `apps/dashboard` | `VITE_API_BASE=https://aivis-api.onrender.com`<br>`VITE_MES_API_BASE=https://aivis-mes.onrender.com`<br>`VITE_HMI_URL=https://aivis-hmi.vercel.app` |

- 값은 끝 슬래시 없이. `VITE_WS_URL` 은 **`wss://…/ws/live` 전체**(https 페이지에서 `ws://` 는 막힌다).
- `VITE_HMI_URL` 은 실시간 현황 카드의 "작업자 화면" 링크용 — hmi 배포 후 그 주소를 넣는다.
- ⚠️ **`VITE_*` 는 빌드할 때 박힌다.** 값을 바꾸면 반드시 **Redeploy** 해야 반영된다.
- 배포 후 두 도메인을 기록한다(예: `https://aivis-hmi.vercel.app`, `https://aivis-dashboard.vercel.app`).

---

## 4) CORS 마무리

Render 에서 입력 → 각 서비스 **Manual Deploy**(재시작) — 끝 슬래시 없이, 콤마로.

| 서비스 | `ALLOWED_ORIGINS` |
|---|---|
| aivis-api | `https://aivis-hmi.vercel.app,https://aivis-dashboard.vercel.app` |
| aivis-mes | `https://aivis-dashboard.vercel.app` |

비워 두면 `*` 로 동작하지만 운영에서는 반드시 도메인을 적는다.

---

## 5) 현장 라즈베리파이 연결

파이는 **워커만** 돌린다(API·DB·화면은 클라우드). 독립형 서비스가 켜져 있으면 끈다.

```bash
sudo systemctl disable --now aivis-standalone.service 2>/dev/null || true
sudo install -D -m600 deploy/aivis-worker.env.example /etc/aivis/worker.env
sudo nano /etc/aivis/worker.env      # 아래 값만 고친다
sudo install -m644 deploy/aivis-vision-pi.service /etc/systemd/system/aivis-vision.service
sudo systemctl daemon-reload && sudo systemctl enable --now aivis-vision.service
```

`/etc/aivis/worker.env` (모드 A — 클라우드):

| 키 | 값 |
|---|---|
| `AIVIS_API_URL` | `https://aivis-api.onrender.com` |
| `AIVIS_SERVICE_TOKEN` | 2-5 에서 복사한 값 |
| `AIVIS_STORAGE_BACKEND` | `api` (사진을 api 로 — **파이에 Supabase 키 없음**) |
| `AIVIS_REMOTE_RAW` | `ng` (원본은 NG·재확인·수집용 LOT 만. 나머지는 결과 사진만) |
| `AIVIS_REMOTE_JPEG_QUALITY` | `80` |
| `AIVIS_CAM_ID` | `PI-CAM1`, 2호기는 `PI-CAM2` — **대마다 다르게** |
| `AIVIS_TRIGGER` | **`vision`**(영상 트리거 — 센서 없이 제품이 들어와 멈출 때만 찍는다. 설치 때 검사 영역을 맞춘다: `docs/OPERATIONS_PI.md` §11-1). 타이머는 빈 컨베이어도 올리므로 쓰지 않는다 |
| `AIVIS_ROI` | 검사 영역(조정 도구로 정한 값) — 영역 밖 제품은 검사하지 않고, 결과 사진에 노란 점선 사각형으로 보인다 |
| `AIVIS_SEED_ADMIN_*` | **비워 둔다**(관리자 비밀번호를 파이에 두지 않는다) |

- 영상 트리거의 감시 영상은 파이 안에서만 쓰고 인터넷으로 나가지 않는다(찍은 사진만 올라간다).
- 인터넷이 끊기면 결과·사진을 `AIVIS_SPOOL_DIR` 에 쌓았다가 복구되면 오래된 것부터 보낸다.
- **파이 모니터(키오스크)** 는 클라우드 작업자 화면을 띄운다 — `docs/OPERATIONS_PI.md` 키오스크
  설정의 주소를 `https://aivis-hmi.vercel.app/?cam=PI-CAM1` 로 바꾼다(`?cam=` 로 그 라인만).

---

## 6) 확인 순서

1. `https://aivis-api.onrender.com/health`, `https://aivis-mes.onrender.com/health` → `ok`.
2. 대시보드 로그인(`admin` / 2-3 비밀번호) → **기준정보**에 실제 품목 등록(데모 품목 HP12 는
   첫 연결 시험용).
3. **사용자 계정**: admin 으로 작업자·품질관리자 계정을 만든다(공개 가입 없음):
   ```bash
   curl -s -X POST https://aivis-api.onrender.com/auth/users \
     -H "authorization: Bearer <admin JWT>" -H 'content-type: application/json' \
     -d '{"username":"line1","password":"<강한 비번>","role":"operator"}'
   ```
4. **MES 작업지시** 등록 → **검사기에 오더 내리기** → 파이가 15초 안에 그 품목·LOT 으로 전환.
5. 제품을 통과시키면 → **실시간 현황**에 카메라별 카드·사진, 작업자 화면(Vercel)에 판정.
6. **MES 연계현황**: "아직 안 가져간 건수" 가 10초 안에 0. **KPI** 의 'MES 수신 확인' = 저장 건수.
7. Supabase **Storage → inspection-images** 에 `result/`(전부)와 `raw/`(NG·수집용만)가 쌓인다.

---

## 7) 보안 체크리스트 (공개 전 필수)

- [ ] `AIVIS_SEED_ADMIN_PASSWORD` 강한 값(그룹이 아니라 **api 서비스**에 입력 — 그룹 안의 직접
      입력값은 Render 가 무시해 기본값 `admin1234` 로 떨어진다). 오픈 후 운영 관리자 계정을 따로.
- [ ] `AIVIS_REQUIRE_SERVICE_TOKEN=true`(render.yaml 고정) — 토큰 없이 api 가 뜨지 않는다.
      결과 전송·사진 업로드·`POST /mes/quality` 는 서비스 토큰이 있어야 한다.
- [ ] `ALLOWED_ORIGINS` 를 Vercel 도메인으로 명시(4).
- [ ] service_role 키·DB 주소는 Render(api/mes)에만. Vercel·파이·저장소에 없음.
- [ ] MES REST 수신은 `MES_INTAKE_TOKEN`(자동 생성)으로 잠겨 있다 — 클라우드는 테이블 폴링만 쓴다.
- [ ] 파이 `/etc/aivis/worker.env` 권한 600(서비스 토큰). 파이를 잃어버리면 Render 에서
      `AIVIS_SERVICE_TOKEN` 을 새로 만들고(그룹 값 교체 → api 재배포) 남은 파이에 다시 넣는다.

---

## 8) 용량 · 비용 · 한계

**사진 용량이 가장 먼저 찬다.** 파이 기본 해상도(2304×1296)에서 결과 사진(JPEG 80)은 장당
약 0.2~0.4MB, 원본(JPEG 95)은 약 1MB 다.

| 구성 | 하루(8시간, 파이 1대) | 비고 |
|---|---|---|
| 타이머 1.5초 + 원본 전부(`all`) | 약 25GB | 클라우드에 부적합 |
| 타이머 1.5초 + `ng` | 약 6GB | 결과 사진 위주 |
| 영상 트리거(예: 20초에 한 다발) + `ng` | 약 0.5GB | **권장** |

- **보관기한**: api 가 `AIVIS_REMOTE_RETENTION_DAYS`(기본 90일)보다 오래된 사진을 Supabase 에서
  지운다(검사 기록은 DB 에 남고, 화면에는 "보관기간 경과"). 0 이면 끈다.
- **Supabase 무료**: DB 500MB·Storage 1GB, 1주간 활동 없으면 일시정지. 생산에 붙이면 **Pro** 를 권한다.
- **Render 무료**: 잠듦·월 750시간(2) 참조).
- OK 원본이 클라우드에 없으면 그 행은 원본 사진이 비어 있다(결과 사진은 있다). 학습용 원본은
  수집용 LOT(`TRAIN-…`)으로 찍으면 전부 올라간다(`docs/OPERATIONS_PI.md` §12).
- 현장 인터넷이 끊겨도 검사는 계속된다(파이 스풀). 단, 그동안 **화면에는 새 결과가 안 보인다** —
  화면이 클라우드에 있기 때문이다. 인터넷이 불안정한 현장은 독립형(모드 B)이 맞다.

---

## 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| 화면에 "서버 연결 실패"·CORS 오류 | `ALLOWED_ORIGINS` 누락·끝 슬래시 | 4) 대로 넣고 서비스 재배포 |
| MES 화면만 "MES 서버에 연결할 수 없습니다" | `VITE_MES_API_BASE` 미설정 또는 mes 잠듦·꺼짐 | 대시보드 환경변수 확인 후 Redeploy, mes `/health` |
| "MES 가 로그인을 받지 않습니다" | api 와 mes 의 `JWT_SECRET` 이 다름 | 둘 다 `aivis-shared` 그룹을 쓰는지 확인 |
| 작업자 화면 실시간이 안 흐름 | `VITE_WS_URL` 이 `ws://` 거나 `/ws/live` 누락 | `wss://…/ws/live` 로 고치고 Redeploy |
| 환경변수를 바꿨는데 화면이 그대로 | `VITE_*` 는 빌드 때 박힌다 | Vercel **Redeploy** |
| api 가 기동하지 않음 — `AIVIS_SERVICE_TOKEN` 이 비어 있다 | env 그룹 미연결 | api 의 env 에 `aivis-shared` 그룹 연결 |
| api 기동 실패 — 마이그레이션 오류 | DB 주소·비밀번호 | Session pooler 주소인지, 비밀번호 URL 인코딩 확인 |
| DB 연결 타임아웃 | Direct(IPv6) 또는 Transaction pooler(6543) 주소 | Session pooler(5432) 로 교체 |
| 파이 로그 401 | 서비스 토큰 불일치 | 2-5 값을 `/etc/aivis/worker.env` 에 그대로 |
| 파이 로그 502(사진) | api 가 Supabase 에 못 올림 | api 의 `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`·버킷 이름 확인. 파이는 스풀에 보관 중 |
| 사진 404 | 보관기한 경과, 또는 OK 원본(정책상 안 올림) | 정상. 원본 전부가 필요하면 `AIVIS_REMOTE_RAW=all` |
| 첫 접속이 매우 느림 | Render 무료 잠듦 | 정상. 상시 운영은 Starter |
| 연계현황 "안 가져간 건수" 가 계속 늘어남 | mes 잠듦·꺼짐 또는 DB 주소가 api 와 다름 | mes `/health` 의 `table_intake`, 두 서비스의 `DATABASE_URL` 비교 |

---

## 부록) 파이 없이 클라우드만 시험하기

실물 카메라가 없을 때는 개발 PC 에서 가짜 카메라 워커를 클라우드 api 에 붙인다(운영 DB 에 시험
데이터가 섞이니 **수집용 LOT(`TRAIN-…`) 으로** 하거나 시험용 Supabase 프로젝트를 따로 쓴다):

```bash
cd services/vision
AIVIS_CAMERA=sim AIVIS_API_URL=https://aivis-api.onrender.com \
AIVIS_SERVICE_TOKEN=<2-5 값> AIVIS_STORAGE_BACKEND=api AIVIS_REMOTE_RAW=ng \
AIVIS_CAM_ID=SIM-PC AIVIS_LOT=TRAIN-CLOUD-TEST python -m worker
```
