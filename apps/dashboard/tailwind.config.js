/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // OK/NG 고대비 색 (색약 고려 — 색 단독 의존 금지, 아이콘/라벨 병기).
        ok: { DEFAULT: "#15803d", bg: "#dcfce7", fg: "#14532d" },
        ng: { DEFAULT: "#b91c1c", bg: "#fee2e2", fg: "#7f1d1d" },
        // KPI 게이지 목표 달성/미달.
        pass: "#16a34a",
        warn: "#d97706",
        fail: "#dc2626",
        brand: { DEFAULT: "#0f4c81", fg: "#e6eef5" },
      },
    },
  },
  plugins: [],
};
