/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 현장 가독성: OK/NG 고대비 색(색약 고려 — 색 단독 의존 금지, 아이콘 병기).
        ok: { DEFAULT: "#15803d", bg: "#dcfce7", fg: "#14532d" },
        ng: { DEFAULT: "#b91c1c", bg: "#fee2e2", fg: "#7f1d1d" },
      },
      fontSize: {
        // 대형 디스플레이용 큰 폰트 스케일.
        hmi: ["1.5rem", { lineHeight: "2rem" }],
        "hmi-lg": ["2.25rem", { lineHeight: "2.5rem" }],
        "hmi-xl": ["3.5rem", { lineHeight: "1" }],
        // --- 현장 고정화면(파이 7" 800x480) 유동 스케일 ---
        // 작업자는 설비에서 1~2m 떨어져 힐끗 본다. 판정은 그 거리에서 0.5초
        // 안에 읽혀야 하므로 화면 폭에 비례해 키운다(clamp 로 PC 에서 과대
        // 확대 방지). 800px 기준: verdict≈68px, num≈26px, cap≈12px.
        verdict: ["clamp(3rem, 8.5vw, 6rem)", { lineHeight: "1" }],
        "hmi-num": ["clamp(1.35rem, 3.2vw, 2.25rem)", { lineHeight: "1.15" }],
        "hmi-body": ["clamp(1rem, 2.1vw, 1.5rem)", { lineHeight: "1.3" }],
        "hmi-cap": ["clamp(0.75rem, 1.5vw, 1rem)", { lineHeight: "1.25" }],
      },
      minHeight: {
        // 장갑 낀 손 터치 최소 타겟(현장 HMI 기준 64px).
        touch: "4rem",
      },
    },
  },
  plugins: [],
};
