#!/usr/bin/env python3
"""정적 SPA(단일 페이지 앱) 서버 — 빌드된 프론트(dist)를 파이에서 서빙.

Node/serve 없이 표준 라이브러리만으로 SPA 히스토리 폴백을 처리한다:
파일이 존재하면 그 파일을, 없으면 index.html 을 반환(딥링크 404 방지).
독립형(모드 B)에서 7" LCD 키오스크가 바라보는 HMI 서버로 사용한다.

사용:
    python3 scripts/serve-spa.py apps/hmi/dist 5173
    # 이후 chromium-browser --kiosk http://localhost:5173
"""
from __future__ import annotations

import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class SPAHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        # 요청 경로를 실제 파일로 변환. 없으면 index.html 로 폴백(SPA 라우팅).
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if not os.path.exists(path):
            self.path = "/index.html"
        return super().do_GET()

    def end_headers(self) -> None:
        # 개발/현장 편의: 정적 자산 캐시 최소화(재배포 즉시 반영).
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main(argv: list[str]) -> int:
    directory = argv[1] if len(argv) > 1 else "apps/hmi/dist"
    port = int(argv[2]) if len(argv) > 2 else 5173
    if not os.path.isfile(os.path.join(directory, "index.html")):
        print(f"[serve-spa] '{directory}/index.html' 없음 — 먼저 빌드하라: "
              f"npm run build --workspace @aivis/hmi", file=sys.stderr)
        return 1
    handler = partial(SPAHandler, directory=directory)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"[serve-spa] {directory} → http://localhost:{port} (Ctrl+C 종료)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve-spa] 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
