import { describe, expect, it } from "vitest";
import { hmiUrlFor, resolveApiBase, resolveHmiBase, resolveMesBase } from "./env";

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

describe("resolveHmiBase / hmiUrlFor (실시간 현황 → 그 라인 작업자 화면)", () => {
  it("명시값이 최우선(클라우드: 다른 도메인)", () => {
    expect(resolveHmiBase("https://hmi.example.com/", undefined)).toBe("https://hmi.example.com");
  });
  it("독립형(:5174)이면 같은 호스트 :5173", () => {
    expect(
      resolveHmiBase(undefined, { protocol: "http:", hostname: "192.168.0.31", port: "5174" }),
    ).toBe("http://192.168.0.31:5173");
  });
  it("80/443 프록시인데 명시가 없으면 모른다 → null(틀린 링크 대신 숨김)", () => {
    expect(resolveHmiBase(undefined, { protocol: "https:", hostname: "x", port: "" })).toBeNull();
  });
  it("카메라 고정 주소", () => {
    expect(hmiUrlFor("PI-CAM2", "http://h:5173")).toBe("http://h:5173/?cam=PI-CAM2");
    expect(hmiUrlFor("PI-CAM2", null)).toBeNull();
  });
});

describe("resolveMesBase (MES 는 별도 서비스 :8100 — same-origin 으로 둘 수 없다)", () => {
  const pi = { protocol: "http:", hostname: "192.168.0.42", port: "5174" };

  it("VITE_MES_API_BASE 명시 시 그대로(후행 / 제거)", () => {
    expect(resolveMesBase("http://mes.example.com/", pi)).toBe("http://mes.example.com");
    expect(resolveMesBase("/custom-mes/", pi)).toBe("/custom-mes");
  });

  it('명시적 빈값("")도 그대로 허용(MES 를 같은 출처 루트에 물린 배포)', () => {
    expect(resolveMesBase("", pi)).toBe("");
  });

  it("미지정 + SPA 포트(5174/5173) → 같은 호스트 :8100", () => {
    expect(resolveMesBase(undefined, pi)).toBe("http://192.168.0.42:8100");
    expect(
      resolveMesBase(undefined, { protocol: "https:", hostname: "dev.local", port: "5173" }),
    ).toBe("https://dev.local:8100");
  });

  it("미지정 + 80/443/무포트 → /mes-api (역프록시 관례)", () => {
    for (const port of ["", "80", "443"]) {
      expect(resolveMesBase(undefined, { protocol: "https:", hostname: "a.io", port })).toBe(
        "/mes-api",
      );
    }
  });

  it("호스트를 모르면(비브라우저) /mes-api", () => {
    expect(resolveMesBase(undefined, { protocol: "", hostname: "", port: "" })).toBe("/mes-api");
  });
});
