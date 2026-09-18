import { describe, expect, it } from "vitest";
import { defaultApiBase, withWsToken } from "@/lib/config";

describe("defaultApiBase (실사용: 접속 호스트 기준 API 결정)", () => {
  it("파이 LCD(localhost) → localhost:8000", () => {
    expect(defaultApiBase({ protocol: "http:", hostname: "localhost" })).toBe(
      "http://localhost:8000",
    );
  });

  it("사무실 PC(파이 IP 접속) → 같은 IP 의 :8000 (localhost 고정 금지 회귀)", () => {
    expect(
      defaultApiBase({ protocol: "http:", hostname: "192.168.0.42" }),
    ).toBe("http://192.168.0.42:8000");
  });

  it("비브라우저 환경 폴백 → localhost:8000", () => {
    expect(defaultApiBase(undefined)).toBe("http://localhost:8000");
  });
});

describe("withWsToken", () => {
  it("토큰을 query 로 부착", () => {
    expect(withWsToken("ws://h:8000/ws/live", "T")).toBe(
      "ws://h:8000/ws/live?token=T",
    );
  });
});
