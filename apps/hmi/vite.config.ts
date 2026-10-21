import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// AIVIS HMI — build outDir 기본값 dist/ → devops nginx Dockerfile(/usr/share/nginx/html) 계약 일치.
//
// AIVIS_HMI_BASE(예 "/hmi/"): 한 도메인의 하위 경로에 물리는 배포(Vercel services, 2026-10-21).
// 자원 주소가 그 경로로 시작해야 하고, Vercel 은 요청 경로를 그대로 넘기므로 산출물도
// dist/hmi/ 아래에 둔다(dist 를 내보내면 /hmi/index.html·/hmi/assets/… 가 된다). 비우면 종전대로 "/".
const base = process.env.AIVIS_HMI_BASE || "/";
const outDir = base === "/" ? "dist" : `dist${base.replace(/\/$/, "")}`;

export default defineConfig({
  base,
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
  },
  build: {
    outDir,
    sourcemap: false,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
