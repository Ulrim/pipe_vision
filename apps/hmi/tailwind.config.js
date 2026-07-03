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
      },
    },
  },
  plugins: [],
};
