/**
 * Vitest 전역 셋업 — jest-dom 매처 등록.
 * vite.config.ts test.setupFiles 에서 로드.
 */
import "@testing-library/jest-dom/vitest";

// jsdom 은 URL.createObjectURL/revokeObjectURL 를 구현하지 않는다.
// 인증 이미지 훅(useAuthedImage)이 objectURL 을 만드므로 기본 스텁을 둔다.
// (개별 테스트에서 vi.spyOn 으로 호출을 검증할 수 있다.)
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:mock";
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
}
