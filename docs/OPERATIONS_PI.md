# AIVIS 라즈베리파이 현장 운영 가이드 (현장 담당자용)

> 이 문서는 **리눅스를 몰라도** 파이에서 AIVIS 를 켜고, 보고, 업데이트할 수 있게
> 쓴 실무 안내서다. 명령은 그대로 복사해서 붙여넣으면 된다.
> 설치·카메라 세팅 등 기술 상세는 [`docs/RASPBERRY_PI.md`](RASPBERRY_PI.md),
> 서버/도커 운영은 [`docs/OPERATIONS.md`](OPERATIONS.md) 를 본다.

---

## 0. 한 장 요약 (이것만 외우면 됨)

| 하고 싶은 일 | 방법 |
|---|---|
| **처음 설치** (한 번만) | `bash ~/pipe_vision/scripts/aivis-install.sh` — 자동실행 등록까지 한 번에 |
| **프로그램 최신으로** | 관리자 대시보드 → **프로그램 업데이트** → 버튼 클릭 (터미널 불필요) |
| 뭐든 조작(메뉴) | `bash ~/pipe_vision/scripts/aivis.sh` |
| 지금 상태 보기 | `bash ~/pipe_vision/scripts/aivis.sh status` |
| 실시간 모니터 | `bash ~/pipe_vision/scripts/aivis.sh monitor` |
| 사무실 PC 접속 주소 | `bash ~/pipe_vision/scripts/aivis.sh urls` |

> `~/pipe_vision` 은 저장소를 내려받은 위치다. `/opt/aivis` 등 다른 곳에 두었으면
> 그 경로로 바꿔 읽는다. 아래 예시는 모두 **저장소 폴더 안에서** 실행한다고 가정한다:
> ```bash
> cd ~/pipe_vision
> ```

---

## 1. 최초 설치 (한 번만)

### 1-1. 준비물
- 라즈베리파이 4 (4GB) + Raspberry Pi OS 64-bit(Bookworm)
- **카메라 — 스테이션에 따라 다릅니다(중요)**
  - **길이 검사(±0.1mm)**: **HQ Camera(IMX477) + C마운트 렌즈 f=16~25mm**.
    Camera Module 3 는 렌즈가 고정이라 작업거리가 22cm 로 강제되고, 그
    거리로는 공차를 못 맞춥니다(%GR&R 73%). 자세한 계산 §1-3 ⓪.
  - **표면·계수 검사**: Camera Module 3 (IMX708) 으로 충분합니다.
- **15.6인치 FHD 포터블 모니터**(2026-10-02 확정) + HDMI 케이블
- 정품 전원어댑터 — 전원 부족은 오작동의 가장 흔한 원인이다
- **길이 검사 스테이션이면 추가로**: 기준자(등간격 마크 막대), 체커보드
  인쇄물(렌즈 보정용), 온도계. 이유는 §1-3 참조
- 파이가 사내 네트워크(유선 권장)에 연결되어 있을 것

### 1-2. 설치 — 명령 한 줄

파이에 SSH로 접속하거나 파이 화면에서 터미널을 열고, 아래를 **그대로 복사해
붙여넣으세요.** 설치부터 부팅 자동실행 등록까지 한 번에 끝납니다.

```bash
sudo apt update && sudo apt install -y git

# 처음이든 다시 하든 이 블록 그대로 쓰면 됩니다.
if [ -d ~/pipe_vision/.git ]; then
  cd ~/pipe_vision && git fetch origin main && git checkout -B main origin/main
elif [ -e ~/pipe_vision ]; then
  echo "~/pipe_vision 이 있는데 git 저장소가 아닙니다. 아래 '폴더가 이미 있다면' 참조"
else
  git clone https://github.com/Ulrim/pipe_vision.git ~/pipe_vision && cd ~/pipe_vision
fi

cd ~/pipe_vision && bash scripts/aivis-install.sh
```

> 중간에 **관리자 비밀번호**를 한 번 물어봅니다(파이 로그인 비밀번호).
> 파이에서는 **20~30분** 걸립니다(화면 만들기가 오래 걸립니다). 끝날 때까지
> 창을 닫지 마세요. `sudo` 를 앞에 붙이지 마세요 — 스크립트가 알아서 씁니다.

#### `destination path '/home/pi/pipe_vision' already exists` 가 나왔다면

폴더가 이미 있어서 `git clone` 이 거부된 것입니다. **정상이고, 지울 필요
없습니다.** 위 블록을 쓰면 자동으로 처리되지만, 이미 옛 명령을 쓰셨다면:

```bash
cd ~/pipe_vision
git fetch origin main
git checkout -B main origin/main
bash scripts/aivis-install.sh
```

그래도 안 되면 무엇이 들어 있는지부터 봅니다:

```bash
ls -a ~/pipe_vision | head        # .git 이 보이면 저장소가 맞습니다
cd ~/pipe_vision && git status    # 아니면 "not a git repository"
```

- **`.git` 이 있고 `git status` 가 동작** → 위 4줄이면 됩니다.
- **로컬 수정이 있어 checkout 이 거부됨** → 현장 파이에 손댄 게 없다면
  버려도 됩니다: `git reset --hard && git clean -fd` 후 다시.
  `.venv/` 와 `node_modules/` 는 gitignore 대상이라 **지워지지 않습니다**
  (다시 빌드하느라 20~30분을 또 쓰지 않아도 됩니다).
  ⚠️ **`-x` 를 붙이지 마세요** — `git clean -fdx` 는 그 둘까지 날립니다.
  검사 이미지·DB 는 저장소 밖(`/var/lib/aivis`)이라 어느 쪽이든 안전합니다.
- **git 저장소가 아님**(이전 복사본·압축 해제본) → 비켜놓고 새로 받습니다:
  `mv ~/pipe_vision ~/pipe_vision.old && git clone https://github.com/Ulrim/pipe_vision.git ~/pipe_vision`

설치가 하는 일:

| 단계 | 내용 |
|---|---|
| 1/6 | 준비 확인 (시스템·카메라·디스크 여유) |
| 2/6 | 필요한 프로그램 설치 (python, 카메라·영상 라이브러리, node) |
| 3/6 | 검사 프로그램 환경 만들기 |
| 4/6 | 화면 만들기 (작업자 화면 + 관리자 대시보드) |
| 5/6 | **부팅 자동실행 등록** — 전원만 켜면 검사 시작 |
| 6/6 | 시작 + 접속 주소 안내 |

끝나면 이런 안내가 나옵니다:

```
===============================================================
  설치가 끝났습니다
---------------------------------------------------------------
   작업자 화면 (HMI)   http://192.168.0.42:5173
   관리자 대시보드      http://192.168.0.42:5174
   파이 화면에서는      http://localhost:5173

   로그인   아이디: admin   비밀번호: aivis1234
   첫 로그인 후 비밀번호를 바꾸세요.
---------------------------------------------------------------
   전원을 켜면 자동으로 시작됩니다.
===============================================================
```

**이제 파이 전원을 껐다 켜도 검사가 저절로 시작됩니다.** 확인하려면 파이를
재부팅한 뒤 1~2분 기다렸다가 위 주소로 접속해 보세요.

#### 설치가 도중에 멈췄다면
같은 명령을 **다시 실행**하면 됩니다(이미 끝난 단계는 건너뜁니다).
실패 메시지에 원인과 해결 방법이 함께 나옵니다.

#### 자주 나오는 안내
- `카메라 라이브러리(picamera2)가 없어 실제 촬영은 되지 않습니다`
  → `sudo apt install python3-picamera2` 실행 후 설치 명령 재실행.
- `라즈베리파이가 아닌 환경으로 보입니다` → 파이가 아닌 PC에서 실행한 경우.
  시험용으로는 그대로 진행해도 됩니다(카메라 없이 시뮬레이터 동작).

#### 옵션 (필요할 때만)
```bash
bash scripts/aivis-install.sh --no-service   # 자동실행 등록 없이 설치만
bash scripts/aivis-install.sh --no-build     # 화면 만들기 건너뛰기(빠름)
bash scripts/aivis-install.sh --help         # 도움말
```

---

## 1-3. 설치 후 보정 — **길이를 재려면 필수**

설치만 하면 검사는 돌지만, **길이값은 아직 믿을 수 없습니다.** 아래 네 가지를
해야 공차 ±0.1mm 를 맞출 수 있습니다(근거: `docs/LENGTH_TOLERANCE.md`).

표면(유분기·변색·스크래치)만 볼 스테이션이면 ①만 하고 넘어가도 됩니다.

### ⓪ 카메라를 제품에서 얼마나 떨어뜨리나 — 설치 전에 정할 것

**최소 80cm, 권장 110~120cm** (제품 250mm 기준). 계산:
`python -m vision.tools.length_budget --length 250 --tol 0.1 --optics`

흔한 오해부터 짚으면 — **시야가 같으면 멀리 둔다고 분해능이 나빠지지
않습니다.** 멀리 두는 만큼 렌즈를 길게 쓰므로 화면에 찍히는 크기는 같습니다.
거리가 바꾸는 건 깊이 민감도(`오차 = 길이 × Δz ÷ 거리`)뿐이라
**멀수록 유리하고 손해가 없습니다.**

| 거리 | %GR&R | |
|---|---|---|
| 20cm | 78% | ❌ |
| 60cm | 33% | ❌ |
| **80cm** | **29%** | 최소 |
| **120cm** | **26%** | 권장 |

**그래서 Camera Module 3 로는 길이를 못 잽니다.** 렌즈가 f=4.74mm 로 고정이라
시야 287mm 를 담으면 거리가 **22cm 로 강제**되고, 그 거리의 %GR&R 은 73%
입니다. 더 멀리 두면 시야가 같이 넓어져 분해능을 잃으니 해결이 안 됩니다.

| 구성 | 거리 | %GR&R |
|---|---|---|
| Camera Module 3 (고정렌즈) | 22cm 강제 | 73% ❌ |
| HQ + C마운트 **f=16mm** | **75cm** | 30% (경계) |
| HQ + C마운트 **f=25mm** | **117cm** | 26% ✅ |

설치 전 확인: 컨베이어면에서 117cm 위에 달려면 프레임이 150cm 이상 필요합니다.
천장·설비 간섭을 먼저 보세요. 다발 폭은 215mm 까지 한 화면에 담깁니다.

### ① 렌즈 왜곡 보정 — 안 하면 길이가 mm 단위로 틀어진다

파이 카메라는 광각이라 **화면 가장자리가 눌려 보입니다.** 길이는 하필 양 끝단,
즉 가장자리를 씁니다. 보정 전 오차는 **0.5~2.5mm** 로 공차의 5~25배입니다.

**체커보드는 만들어 두었습니다** — `docs/targets/checkerboard_A3_9x6_30mm.pdf`
(A4 판도 있음). 인쇄 주의사항은 `docs/targets/README.md` 를 꼭 읽으세요.

```bash
# 1) 인쇄: 배율 100%(실제 크기), "용지에 맞춤" 끄기.
#    인쇄 후 시트 아래 VERIFY SCALE 선이 자로 200.00mm 인지 확인.
#    **평평한 판에 주름 없이 붙이세요** — 종이가 휘면 휜 모양을 왜곡으로
#    오인해서, 보정이 오히려 나빠집니다.

# 2) 15~20장 촬영 — 각도·거리·화면 위치를 바꿔가며 **한 폴더에** 모읍니다.
#    화면 네 귀퉁이에 보드가 걸친 장면을 꼭 넣으세요. 왜곡은 가장자리가
#    제일 심한데 가운데 사진만 모으면 거기를 못 고칩니다.
#    초점은 운영과 **똑같이 고정**한 채로 찍습니다.
mkdir -p ~/calib_shots
for i in $(seq 1 20); do
  rpicam-still -o ~/calib_shots/chk_$i.jpg --width 4608 --height 2592 -t 1500
  echo "  $i/20 — 보드 위치를 바꾸고 Enter"; read
done

# 3) 보정 계산 (--images 는 **폴더**를 받습니다)
cd ~/pipe_vision/services/vision
.venv/bin/python -m vision.tools.calibrate_lens \
    --images ~/calib_shots --cols 9 --rows 6 --square-mm 30 \
    --out /etc/aivis/lens.json

# 4) 워커에 알려주고 재시작
sudo sh -c 'echo AIVIS_LENS_CALIB=/etc/aivis/lens.json >> /etc/aivis/worker.env'
sudo systemctl restart aivis-vision-pi
```

> `--cols`/`--rows` 는 **내부 코너 수**입니다(칸 수 아님). 제공한 보드는 10×7
> 칸이라 9×6 입니다. `--square-mm` 은 A3 판이 30, A4 판이 20 입니다.
>
> **RMS 가 1.0px 를 넘으면 다시 찍으세요.** 도구가 경고합니다. 흔한 원인은
> 보드가 휘었거나, 장수가 부족하거나, 가장자리 사진이 없는 경우입니다.

### ② 기준자 설치 — 배율이 흔들려도 길이가 안 흔들리게

제품 높이가 1mm 흔들리면 길이가 **0.5mm** 틀어집니다(250mm·작업거리 500mm).
기준자를 제품과 **함께** 찍으면 기준자도 같이 흔들려 상쇄됩니다.

**기준자도 만들어 두었습니다** — `docs/targets/gauge_A4_10mm.pdf`
(마크 27개 @10mm, 스팬 260mm). 긴 제품은 `gauge_A3_10mm.pdf`(380mm).

> ⚠️ **인쇄 배율 오차가 그대로 측정 오차가 됩니다.** 프린터 ±0.3% 는 250mm 에서
> 0.75mm — 공차의 7배입니다. 그래서 **인쇄한 실제 값을 재서 넣습니다.**

```bash
# 1) 인쇄(100%) → 평평한 판에 붙이기
# 2) 캘리퍼로 양 끝 눈금 중심 사이(스팬)를 잰다. 시트에 기입란이 있습니다.
#    예) 259.6mm 가 나왔다면  간격 = 259.6 / (27-1) = 9.9846
# 3) 기준자 띠가 화면에서 차지하는 영역을 확인
rpicam-still -o /tmp/f.jpg --width 4608 --height 2592

# 4) 설정: "간격mm:x,y,폭,높이"  ← 간격은 **측정값**을 넣습니다
sudo sh -c 'echo AIVIS_FIDUCIAL=9.9846:0,0,4608,220 >> /etc/aivis/worker.env'
sudo systemctl restart aivis-vision-pi
```

설치 요건 세 가지(하나라도 어기면 효과가 없습니다):

1. **기준자를 제품 상면과 같은 높이에** 두세요. 바닥판에 눕히면 튜브 OD 의
   절반만큼 평면이 달라 **3mm** 계통오차가 납니다(공차의 30배).
   **품목이 바뀌어 OD 가 달라지면 기준자 높이도 바꿔야 합니다.**
2. 기준자가 제품 양 끝보다 **길어야** 합니다. 마크 바깥은 외삽입니다.
3. ROI 는 **마크 행 중심 ±15mm 안**으로. 그 바깥의 글자·눈금이 들어오면
   검출이 깨집니다(시트가 그만큼 비워 두도록 배치돼 있습니다).

설정하면 워커가 **매 프레임** 기준자를 읽어 그 프레임의 배율로 잽니다.
못 읽으면 저장된 `px_to_mm_scale` 로 떨어지되 **로그에 이유가 남습니다** —
길이가 이상할 때 제일 먼저 볼 곳입니다.

```bash
journalctl -u aivis-vision-pi | grep 기준자    # 읽기 실패가 쌓이는지 확인
```

> **양산에서는 강재 자나 유리 눈금자**를 쓰고 성적서 값을 넣으세요. 종이는
> 습도에 따라 0.1~0.2% 늘고 줄어, 그것만으로 250mm 에서 0.25~0.5mm 입니다.
> 인쇄물은 셋업·시운전용입니다. 자세한 내용 `docs/targets/README.md`.

### ③ px→mm 보정계수 (기준자를 안 쓸 때만)

기준자를 설정했다면 건너뛰세요. 안 쓴다면 §7.2(`docs/RASPBERRY_PI.md`)의
자기보정을 쓰거나, 화면에서 `POST /master/items/{code}/calibrate` 로 맞춥니다.

### ④ 온도 — 남은 마지막 제약

알루미늄 250mm 는 **1℃ 에 5.8µm** 늘어납니다. 공차 폭이 0.2mm 이므로 제품
온도가 ±2℃만 흔들려도 공차의 35% 를 온도가 먹습니다.

> **가장 쉬운 해법은 설치 위치입니다 — 길이 스테이션을 세척 이후에 두세요.**
> 제품이 주위 온도와 평형을 이루므로 **공기 온도계 하나**로 ±1℃ 가 됩니다.
> 절단 직후에 두면 톱 발열로 제품마다 온도가 달라 이 방법이 안 통합니다.
> (알루미늄은 방사율이 낮아 비접촉 적외선 온도계는 오히려 부정확합니다.)

### ⑤ 확인 — 숫자로

보정을 마쳤으면 같은 제품을 30회 반복 측정해 실제 반복성을 봅니다.

```bash
cd ~/pipe_vision/services/vision
.venv/bin/python -m vision.tools.run_msa --help
```

**%GR&R 이 30% 를 넘으면 공차를 못 맞춥니다.** 계산상으로는 29% 로 아슬아슬한
경계이므로, 실측으로 반드시 확인하세요. 넘으면 §1-3 ①②④ 중 빠진 것을 찾습니다.

---

## 2. 부팅 자동시작 — 확인 및 해제

§1-2 의 설치 명령을 쓰면 **이미 등록되어 있습니다**. 아래는 확인·변경용입니다.

```bash
sudo bash scripts/aivis-install-service.sh
```

- 현재 저장소 경로와 로그인 사용자를 자동으로 유닛에 반영한다.
- 등록 후에는 크래시가 나도 systemd 가 자동 재시작한다(`Restart=always`).
- 해제하려면:
  ```bash
  sudo bash scripts/aivis-install-service.sh --uninstall
  ```

> ⚠️ **주의**: `aivis-vision.service`(엣지→클라우드 모드, 워커만 실행)와
> `aivis-standalone.service`(이 문서의 독립형)를 **동시에 켜지 마라.**
> 같은 카메라를 두 프로세스가 열어 충돌하고 같은 제품이 두 번 적재된다.
> 설치 스크립트가 감지해 경고한다. 둘 중 하나만:
> ```bash
> sudo systemctl disable --now aivis-vision.service   # 독립형을 쓸 때
> ```

### 2-1. (선택) 모니터 키오스크 자동실행
파이 화면에 작업자 HMI 를 전체화면으로 띄우고 싶을 때:

```bash
sudo apt install -y chromium-browser unclutter
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/aivis-kiosk.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=AIVIS HMI Kiosk
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito http://localhost:5173
X-GNOME-Autostart-enabled=true
EOF
```
데스크톱 로그인 시 자동으로 전체화면 HMI 가 뜬다(종료: `Alt+F4`).
화면이 꺼지지 않게 하려면 `Preferences > Screen Blanking` 을 끈다.

---

## 3. 일상 조작 — 메뉴 하나로

```bash
bash scripts/aivis.sh
```
```
   1) 시스템 시작          2) 시스템 중지
   3) 재시작               4) 상태 보기
   5) 실시간 모니터        6) 프로그램 업데이트
   7) 로그 보기            8) 접속 주소 표시
   0) 종료
```
번호를 누르고 Enter 만 치면 된다. 명령을 직접 쓰고 싶으면:

```bash
bash scripts/aivis.sh start      # 시작
bash scripts/aivis.sh stop       # 중지
bash scripts/aivis.sh restart    # 재시작 (설정을 바꿨을 때)
bash scripts/aivis.sh status     # 상태 1회 요약
bash scripts/aivis.sh monitor    # 실시간 모니터 (Ctrl+C 로 종료)
bash scripts/aivis.sh logs       # 로그 실시간 보기 (Ctrl+C 로 종료)
bash scripts/aivis.sh urls       # 사무실 PC 접속 주소
```

부팅 자동시작을 등록했으면 systemd 로, 안 했으면 백그라운드 직접 실행으로
**알아서 분기**하므로 담당자는 신경 쓸 필요가 없다.

---

## 4. 접속 주소 확인 (사무실 PC 에서 보기)

```bash
bash scripts/aivis.sh urls
```
```
   작업자 화면 (HMI)   http://192.168.0.31:5173
   관리자 대시보드      http://192.168.0.31:5174
   API/문서             http://192.168.0.31:8000   (문서: /docs)
   로그인   아이디: admin   비밀번호: aivis1234
```
- 파이의 실제 IP 를 읽어 표시한다. 사무실 PC 브라우저 주소창에 그대로 입력한다.
- 파이와 PC 가 **같은 네트워크**에 있어야 한다.
- 비밀번호는 최초 로그인 후 반드시 변경한다(대시보드 > 사용자 관리).
- IP 가 자꾸 바뀌면 공유기에서 파이 MAC 에 **고정 IP(DHCP 예약)** 를 설정한다.

---

## 5. 프로그램 업데이트 (개발사가 새 버전을 올렸을 때)

### 5-0. 권장 — 웹 화면에서 버튼으로 (터미널 불필요)

관리자 대시보드 → **프로그램 업데이트** 메뉴에서 버튼만 누르면 됩니다.
터미널을 열 필요가 없어 현장 담당자가 직접 할 수 있습니다.

1. 사무실 PC 브라우저에서 `http://<파이IP>:5174` 접속 (주소는 §4 참고)
2. **관리자 계정**으로 로그인 (작업자·품질관리자 계정은 이 메뉴가 안 보입니다 —
   교대 중에 실수로 눌러 검사가 멈추는 것을 막기 위해서입니다)
3. 메뉴 → **프로그램 업데이트**
4. **[새 버전 확인]** → 새 버전이 있으면 **[지금 업데이트]**
5. 진행 상황이 화면에 표시됩니다. 중간에 화면 연결이 잠시 끊기는 것은
   정상입니다(프로그램이 다시 시작되는 구간). 끝나면 **[화면 새로고침]**.

> **먼저 §2 의 부팅 자동시작을 등록해 두세요.** 등록돼 있어야 업데이트 후
> 프로그램이 스스로 다시 시작해 새 버전이 곧바로 적용됩니다. 등록돼 있지
> 않으면 화면이 "다시 시작해야 적용됩니다" 라고 알려주며, 그때는 아래
> §5-1 의 `bash scripts/aivis.sh restart` 를 한 번 실행해야 합니다.

### 5-1. 터미널에서 (원격 점검·문제 해결용)

```bash
cd ~/pipe_vision
bash scripts/aivis-update.sh
```

진행 화면 예:
```
[1/5] 사전 점검 — 현재 상태 확인
[2/5] 최신 코드 받는 중…
[3/5] 변경 내용 분석 — 다시 만들어야 할 부분만 고릅니다
[4/5] 필요한 부분만 다시 만드는 중…
[5/5] 서비스 재시작
```

이 스크립트가 알아서 해 주는 것:
- 파이에서 손댄 파일이 있으면 **지우지 않고 보관**(git stash)하고 복구 명령을 알려준다.
- 바뀐 부분만 다시 만든다(화면만 바뀌었으면 파이썬 설치를 건너뛰어 시간 절약).
- 실패하면 **어느 단계에서 왜** 실패했는지와 되돌리는 방법을 출력한다.

자주 쓰는 옵션:
```bash
bash scripts/aivis-update.sh --dry-run     # 무엇이 바뀔지 미리보기(실제 변경 없음)
bash scripts/aivis-update.sh --restart     # 업데이트 후 묻지 않고 재시작
bash scripts/aivis-update.sh --rollback    # 직전 업데이트 이전 상태로 되돌리기
bash scripts/aivis-update.sh --help        # 전체 도움말
```

### 5-2. 업데이트가 실패했을 때
1. 화면에 나온 **[실패]** 줄과 그 아래 안내를 그대로 캡처해 개발사에 전달한다.
2. 급하게 생산을 돌려야 하면 이전 버전으로 되돌린다:
   ```bash
   bash scripts/aivis-update.sh --rollback
   bash scripts/aivis.sh restart
   ```
3. 인터넷이 안 되는 현장이면 업데이트는 실패한다(정상). 사무실에서 USB 로 코드를
   받아오거나, 파이를 잠시 인터넷 되는 망에 연결한 뒤 다시 시도한다.

> 검사 데이터(DB·이미지·미전송 스풀)는 `/var/lib/aivis` 에 있고 git 관리 밖이라
> 업데이트/롤백으로 **절대 사라지지 않는다.**

---

## 6. 모니터링

### 6-1. 파이 터미널에서 (화면·SSH 둘 다)
```bash
bash scripts/aivis.sh monitor          # 2초마다 갱신, Ctrl+C 종료
bash scripts/aivis.sh status           # 1회만 보고 끝
```
표시 내용:
- **라즈베리파이 상태**: CPU 온도/사용률/메모리/디스크 (게이지 + 수치)
- **서비스**: API / 데이터베이스 / 검사 워커 → `정상 / 응답지연 / 정지`
- **검사 실적**: 최근 1시간·오늘 검사수·NG·불량률, 처리속도(평균·p95, 목표 300ms), MES 미전송 건수
- **최근 오류**: 마지막 오류 몇 건

상태는 **색 + 기호 + 한국어** 로 함께 표기한다(`[O] 정상`, `[!] 주의`, `[X] 위험`).
색이 안 보이는 화면이나 색약이어도 기호·글자로 판독할 수 있다.

> **API 가 죽어 있어도** 이 모니터는 동작한다. 그때가 가장 필요한 순간이므로,
> 파이에서 직접 읽는 CPU 온도·부하·메모리·디스크는 계속 표시된다.

옵션:
```bash
python3 scripts/aivis-monitor.py --once                 # 1회 출력
python3 scripts/aivis-monitor.py --interval 5           # 5초 주기
python3 scripts/aivis-monitor.py --url http://다른파이:8000
python3 scripts/aivis-monitor.py --user admin --password <비밀번호>
```

### 6-2. 웹 페이지에서 (사무실 PC)
관리자 대시보드(`http://<파이IP>:5174`) 의 **모니터** 화면에서 같은 지표를 그래프로
본다. 여러 사람이 동시에 보기 좋고, 파이 앞에 갈 필요가 없다.

### 6-3. 무엇을 봐야 하나
| 지표 | 정상 | 조치가 필요한 값 |
|---|---|---|
| CPU 온도 | 60℃ 이하 | **70℃↑ 주의**(환기·방열판), **80℃↑ 위험**(성능 저하·정지 위험) |
| 디스크 | 70% 이하 | **85%↑** 오래된 이미지 정리 필요 |
| 검사 워커 | 정상 | **응답지연/정지** → 재시작(3번) |
| 처리속도 p95 | 300ms 이하 | 초과 지속 시 개발사 문의(ROI·모델 조정) |
| MES 미전송 | 0 | 계속 쌓이면 네트워크/MES 점검 |

---

## 7. 자주 겪는 문제와 대처

### 7-1. 카메라를 못 잡는다
증상: 로그에 `camera`/`picamera2` 오류, 워커가 계속 재시작.
```bash
# 1) 카메라가 하드웨어로 보이는지
rpicam-hello --list-cameras          # 구형: libcamera-hello --list-cameras

# 2) 안 보이면: 리본 케이블 방향/접촉 확인 후 전원 껐다 켜기(재부팅 아님, 완전 종료)
sudo shutdown -h now

# 3) 사용자 권한(카메라는 video 그룹 필요)
sudo usermod -aG video $USER && sudo reboot

# 4) 다른 프로세스가 카메라를 점유했는지 (두 유닛 동시 실행 금지!)
systemctl is-active aivis-vision.service aivis-standalone.service
```
급할 때는 카메라 없이 시뮬레이터로 계속 검증할 수 있다:
```bash
AIVIS_CAMERA=sim bash scripts/aivis.sh restart
```

### 7-2. 디스크가 가득 찼다
증상: 모니터 디스크 85%↑, 저장 실패 로그, 업데이트·설치가 `npm install 실패` 로 끝남.

**원인은 거의 항상 검사 이미지다.** 검사 1건마다 원본(raw)과 판정(result)
이미지가 저장되고, 경계값으로 판정된 건은 재확인본(review)까지 한 장 더 남는다.
1.5초 간격으로 돌리면 **하루 5~8GB** 씩 쌓인다.

**지금 프로그램은 스스로 정리한다** (기본값: 원본 2일 / 양품 판정 7일 /
불량 판정 180일 / 재확인본 730일, 남은 공간이 2GB 밑으로 떨어지면 오래된 것부터
추가 삭제). 아래는 **이미 가득 차서 업데이트조차 못 받는 장비**를 구제할 때.

```bash
# 1) 무엇이 얼마나 먹고 있는지
df -h /var/lib/aivis
du -sh /var/lib/aivis/images/*        # raw / result / review 별 용량

# 2) 정리 — 먼저 "무엇을 지울지"만 보여준다
bash scripts/aivis-clean-images.sh

# 3) 확인했으면 실제 삭제
bash scripts/aivis-clean-images.sh --yes
```
검사 이력(DB)·통계·KPI 는 그대로 남는다. 사라지는 것은 이미지뿐이고,
화면에서는 "이미지 없음" 으로 표시된다.

보관 기간을 바꾸려면 `/etc/default/aivis` (또는 서비스 환경파일)에 넣는다:

| 환경변수 | 기본 | 뜻 |
|---|---|---|
| `AIVIS_RETAIN_RAW_DAYS` | 2 | 원본 이미지 보관일 |
| `AIVIS_RETAIN_OK_DAYS` | 7 | 양품 판정 이미지 보관일 |
| `AIVIS_RETAIN_NG_DAYS` | 180 | 불량 판정 이미지 보관일 |
| `AIVIS_RETAIN_REVIEW_DAYS` | 730 | 재확인(재학습용) 이미지 보관일 |
| `AIVIS_DISK_MIN_FREE_MB` | 2000 | 이 밑으로 떨어지면 긴급 정리 |
| `AIVIS_RETENTION_ENABLED` | true | 자동 정리 끄기(`false`) |

0 으로 두면 그 분류는 기간으로 지우지 않는다(긴급 정리 대상에는 여전히 포함).

근본 대책: 외장 SSD/USB 를 `/var/lib/aivis` 로 마운트하면 보관 기간을 넉넉히
늘릴 수 있다. 백업은 `bash scripts/backup.sh`.

### 7-2-1. 검사 모드 (길이 / 표면 / 개수) — 한 모드는 한 가지만 봅니다

세 가지를 한꺼번에 돌리면 **NG 가 왜 났는지 알 수 없습니다**(현장 지적 2026-10-05).
그래서 모드를 나눴습니다. 모드가 안 보는 항목은 계산도 하지 않고 비워 둡니다.

| 모드 | 보는 것 | NG 사유 표기 예 |
|---|---|---|
| **길이 검사** `CUT_LENGTH` | 길이만 | `길이 −0.18mm (허용 −0.10)` |
| **표면 검사** `POST_WASH_SURFACE` | 유분기·변색·스크래치만 | `유분기 0.62 > 기준 0.40` |
| **개수 확인** `CRATE_COUNT` | 크레이트 단면 개수만 | `개수 18 / 기준 20 (−2)` |

**지금 어떤 모드인지**는 작업자 화면 왼쪽 위 검은 배지에 크게 나옵니다.

**바꾸는 법 두 가지**

1. **화면에서(권장, 재시작 없음)** — 작업자 화면 `오더 설정` → 맨 위 `검사 모드`
   세 버튼 중 하나를 누릅니다. 15초 안에 워커가 따라옵니다. 작업자 권한으로 됩니다.
2. **스테이션 고정(설치 때 한 번)** — 서비스 환경파일에 기본 모드를 적습니다:
   ```bash
   # /etc/aivis/worker.env
   AIVIS_INSPECTION_STAGE=CUT_LENGTH          # 컨베이어 길이 스테이션
   # AIVIS_INSPECTION_STAGE=POST_WASH_SURFACE # 세척 후 표면 스테이션
   # AIVIS_INSPECTION_STAGE=CRATE_COUNT       # 크레이트 개수 확인
   ```
   화면에서 바꾼 값이 있으면 그것이 우선합니다. 화면 설정을 비우면(`PUT /master/active`
   에 `inspection_stage` 없이 저장) 다시 이 기본값으로 돌아옵니다.

> 개수 확인 모드는 **크레이트를 위에서 찍은 단면 사진**을 셉니다(파이프를 세워
> 담은 상자를 위에서 본 것). 기준 개수는 오더 설정의 `한 판 개수`입니다.

### 7-3. 검사 워커가 멈췄다("정지"/"응답지연")
```bash
bash scripts/aivis.sh logs        # 마지막 오류 확인 (Ctrl+C 로 빠져나옴)
bash scripts/aivis.sh restart     # 재시작
bash scripts/aivis.sh status      # 다시 확인
```
재시작해도 반복되면 로그 마지막 30줄을 캡처해 개발사에 전달한다.

### 7-4. 화면(HMI/대시보드)이 안 열린다
```bash
bash scripts/aivis.sh urls        # 주소·IP 가 맞는지
bash scripts/aivis.sh status      # API 응답 여부
ping <파이IP>                     # 사무실 PC 에서 파이가 보이는지
```
- API 는 되는데 화면만 안 뜨면 화면 빌드가 없는 경우다 → `npm run build` 후 재시작.
- 파이 IP 가 바뀐 경우가 가장 흔하다 → 공유기에서 고정 IP 를 잡아 준다.

### 7-5. 전원 경고(스로틀)가 뜬다
모니터에 `전원/발열 스로틀 발생` 이 보이면 **정품 5V/3A 어댑터**를 쓰고 있는지,
USB 허브·연장선을 거치지 않는지 확인한다. 전원 부족은 카메라 오류·데이터 손상의
흔한 원인이다.

### 7-6. 로그인 비밀번호를 잊었다
`AIVIS_ADMIN_PASSWORD` 로 시드된 관리자 계정을 쓴다. 값을 모르면 개발사에 문의한다
(초기값 `aivis1234`). 운영 중 변경한 비밀번호는 DB 에만 있으므로 개발사도 복구할 수
없고, 관리자 계정 재시드가 필요하다.

---

## 8. 참고 — 관련 파일

| 파일 | 용도 |
|---|---|
| `scripts/aivis.sh` | 통합 조작 메뉴(시작/중지/상태/모니터/업데이트/로그/주소) |
| `scripts/aivis-update.sh` | 원클릭 업데이트 + 롤백 |
| `scripts/aivis-monitor.py` | 터미널 실시간 모니터(표준 라이브러리만 사용) |
| `scripts/aivis-install-service.sh` | 부팅 자동시작 등록/해제 |
| `scripts/aivis-standalone.sh` | 실제 스택 기동(위 스크립트들이 호출) |
| `deploy/aivis-standalone.service` | 독립형 systemd 유닛(전체 스택) |
| `deploy/aivis-vision-pi.service` | 엣지→클라우드 유닛(워커 전용, 동시 사용 금지) |
| `/var/lib/aivis` | 검사 데이터(DB·이미지·스풀). 백업 대상 |

---

## 9. 표면 결함 모델 — 파이에서 쓰기

### 9-1. 먼저 속도를 재세요 (중요)

표면 이상탐지는 표면을 격자로 나눠 봅니다. 격자를 키우면 **작은 스크래치를 훨씬
잘 잡지만 느려집니다.** 어느 격자까지 쓸 수 있는지는 장비마다 다르므로 **이
파이에서 직접 재야 합니다.** 개발 PC 수치에 배수를 곱한 추정은 맞지 않습니다.

```bash
cd ~/pipe_vision/services
python -m vision.tools.bench_anomaly --image /var/lib/aivis/images/raw/<아무 사진>.jpg
```

실제 촬영본으로 재는 것이 가장 정확합니다. 사진이 없으면 `--size 1600x400` 처럼
예상 표면 크기를 주면 됩니다. 마지막에 **권장 격자**가 나옵니다.

### 9-2. 그 격자로 학습

```bash
python -m vision.models.train_anomaly \
    --ok-dir /경로/정상품사진 --item HP12 --grid <권장격자>
```

격자는 모델 파일에 기록되므로 검사할 때 자동으로 같은 격자가 쓰입니다. 학습과
검사의 격자가 다르면 비교 기준이 어긋나 정상품을 대량 오검합니다.

### 9-3. 격자를 키우면 무엇이 좋아지나

공개 벤치마크(DAGM 2007) 실측입니다. 같은 기술자·같은 방식인데 격자만 다릅니다.

| 격자 | AUROC | 놓친 결함(27개 중) |
|---|---|---|
| 1x1 (표면 전체를 한 덩어리로) | 0.526 | **26개** |
| 2x2 | 0.657 | 24개 |
| 4x4 | 0.793 | 21개 |
| 8x8 | **0.889** | 1개 |

표면 전체를 하나로 보면 작은 결함이 평균에 묻힙니다. 스크래치가 정확히 그런
결함입니다. **격자 1은 AUROC 0.53 으로 동전 던지기 수준이고 결함 27개 중 26개를
놓쳤습니다** — 이 유형의 결함에는 사실상 눈이 없습니다.

(학습 정상 49장 / 평가 51장 기준. 처음 20장으로 쟀을 때는 8x8 이 1.000 이었는데
표본을 2.5배로 늘리자 0.889 로 내려갔습니다. 작은 표본의 완벽한 점수는 믿을 것이
못 됩니다. 반대로 격자 효과는 표본이 커질수록 **더 뚜렷해졌습니다**.)

### 9-4. 속도가 모자라면

순서대로 시도합니다.

1. **표면 ROI 를 줄입니다.** 촬영 구도에서 제품이 프레임을 꽉 채우게 하면 배경
   화소를 계산하지 않아 그만큼 빨라집니다. 가장 효과가 큽니다.
2. 격자를 한 단계 낮춥니다(8 → 6 → 4).
3. 통계 표본 상한을 낮춥니다: `AIVIS_ANOMALY_MAX_STAT_PX=20000` (기본 50000).
   화소를 솎아 통계를 내므로 이미지 해상도는 그대로입니다.

> **하지 말아야 할 것**: 이미지 자체를 축소하는 것. 가는 스크래치가 뭉개져
> 탐지력이 크게 떨어집니다(실측: DAGM 8x8 AUROC 1.000 → 0.708).
