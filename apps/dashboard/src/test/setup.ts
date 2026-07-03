import "@testing-library/jest-dom/vitest";

// jsdom 은 URL.createObjectURL 미구현 → blob 다운로드 테스트용 스텁.
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:mock";
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => undefined;
}

// ECharts/ResizeObserver 미구현 보강.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
