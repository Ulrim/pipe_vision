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
        // --- 현장 고정화면 유동 스케일 ---
        //
        // **2026-10-02: 화면이 7" 800x480 → 15.6" 1920x1080 으로 바뀌었다.**
        // 15.6" FHD 는 약 141 PPI 라 1mm ≈ 5.6px 다. 작업자는 설비에서 1~2m
        // 떨어져 힐끗 본다. 글자 높이 기준은 통상 "보는 거리 / 200" 이 편안한
        // 하한이고 중요 정보는 그 두 배다 — 1.5m 면 7.5mm(≈42px), 중요한 것은
        // 15mm(≈83px).
        //
        // 종전 상한은 작은 화면 기준이라 1920px 에서 전부 상한에 걸려
        // num 36px(6.4mm), body 24px(4.3mm) 로 1.5m 거리에서 작았다. 상한을
        // 올린다. 하한(clamp 의 첫 값)은 그대로 둬 작은 창에서도 깨지지 않는다.
        verdict: ["clamp(3rem, 8.5vw, 8rem)", { lineHeight: "1" }],
        "hmi-num": ["clamp(1.35rem, 3.2vw, 3.5rem)", { lineHeight: "1.15" }],
        "hmi-body": ["clamp(1rem, 2.1vw, 2.25rem)", { lineHeight: "1.3" }],
        "hmi-cap": ["clamp(0.75rem, 1.5vw, 1.25rem)", { lineHeight: "1.25" }],
      },
      minHeight: {
        // 장갑 낀 손 터치 타깃.
        //
        // 업계 가이드는 장갑 착용 시 **최소 12~15mm, 권장 20mm** 로 본다
        // (절단방지 장갑이 손끝 지름을 3~5mm 키운다). 15.6" FHD 141 PPI 에서
        // 1mm ≈ 5.6px 이므로 15mm ≈ 84px, 20mm ≈ 112px.
        //
        // 종전 64px 은 11.5mm 로 **가이드 하한에도 못 미쳤다.** 7" 화면에서는
        // 더 키울 자리가 없었지만 1080px 화면에서는 여유가 있다.
        touch: "5.25rem",      // 84px ≈ 15mm (하한)
        "touch-lg": "7rem",    // 112px ≈ 20mm (권장)
      },
    },
  },
  plugins: [],
};
