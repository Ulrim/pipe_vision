import { describe, expect, it } from "vitest";
import { resolveApiBase } from "./env";

describe("resolveApiBase (실사용: 독립형 :5174 에서 same-origin 금지)", () => {
  const pi = { protocol: "http:", hostname: "192.168.0.42", port: "5174" };

  it("VITE_API_BASE 명시 시 그대로(후행 / 제거)", () => {
    expect(resolveApiBase("http://api.example.com/", pi)).toBe(
      "http://api.example.com",
    );
  });

  it('명시적 빈값("")은 same-origin 강제(역프록시 배포)', () => {
    expect(resolveApiBase("", pi)).toBe("");
  });

  it("미지정 + SPA 포트(5174) → 같은 호스트 :8000 (Unsupported method POST 회귀)", () => {
    expect(resolveApiBase(undefined, pi)).toBe("http://192.168.0.42:8000");
  });

  it("미지정 + 80/443/무포트 → same-origin(프록시 배포)", () => {
    expect(
      resolveApiBase(undefined, { protocol: "https:", hostname: "a.io", port: "" }),
    ).toBe("");
    expect(
      resolveApiBase(undefined, { protocol: "https:", hostname: "a.io", port: "443" }),
    ).toBe("");
  });

  // 참고: "비브라우저 폴백" 분기는 명시적 undefined 인자가 기본 매개변수를
  // 우회하지 못해(jsdom 의 window.location 이 대신 잡힘) 주입으로 검증 불가.
});
