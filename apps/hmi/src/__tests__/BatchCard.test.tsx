import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BatchCard } from "@/components/BatchCard";
import { groupFeed } from "@/lib/batching";
import { makeBatch } from "./factories";

/** BatchCard 는 ImageView(인증 fetch)를 쓴다 → jpeg 빈 응답으로 흘려보냄. */
function stubImageFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(new Blob([]), {
        status: 200,
        headers: { "Content-Type": "image/jpeg" },
      }),
    ),
  );
}

describe("BatchCard (다중 튜브 배치 카드)", () => {
  beforeEach(() => stubImageFetch());
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("배치 요약(총 N개, NG M개)과 튜브 그리드를 렌더한다", () => {
    const batch = groupFeed(makeBatch(4, { ngIdx: [1, 3] }))[0];
    render(<BatchCard batch={batch} />);

    const card = screen.getByTestId("batch-card");
    expect(card).toHaveAttribute("data-verdict", "NG");
    expect(card).toHaveAttribute("data-total", "4");
    expect(card).toHaveAttribute("data-ng", "2");

    // 튜브 셀 4개.
    expect(screen.getAllByTestId("batch-tube")).toHaveLength(4);
    // 재설계: 별도 NG 요약 배너 대신 **초대형 판정**에 NG 개수를 싣고,
    // 총계는 그 아래 한 줄로 둔다(480px 화면에서 배너까지 넣을 자리가 없다).
    expect(screen.getByTestId("batch-verdict-text")).toHaveTextContent("NG 2");
    expect(screen.getByTestId("batch-card")).toHaveTextContent("총 4개 검사");
  });

  it("전체 OK 배치는 NG 요약 배너를 표시하지 않는다", () => {
    const batch = groupFeed(makeBatch(3, { ngIdx: [] }))[0];
    render(<BatchCard batch={batch} />);
    expect(screen.getByTestId("batch-card")).toHaveAttribute(
      "data-verdict",
      "OK",
    );
    // 전량 양품이면 NG 개수가 아니라 '전량 양품' 으로 표기.
    expect(screen.getByTestId("batch-verdict-text")).toHaveTextContent(
      "전량 양품",
    );
  });

  it("NG 튜브 클릭 시 해당 튜브로 재확인을 요청한다", () => {
    const batch = groupFeed(makeBatch(3, { ngIdx: [2] }))[0];
    const onReview = vi.fn();
    render(<BatchCard batch={batch} onReview={onReview} />);

    const ngTube = screen
      .getAllByTestId("batch-tube")
      .find((el) => el.getAttribute("data-verdict") === "NG")!;
    fireEvent.click(ngTube);
    expect(onReview).toHaveBeenCalledTimes(1);
    expect(onReview.mock.calls[0][0].tube_index).toBe(2);
  });

  it("OK 튜브는 클릭해도 재확인을 트리거하지 않는다(비활성)", () => {
    const batch = groupFeed(makeBatch(3, { ngIdx: [2] }))[0];
    const onReview = vi.fn();
    render(<BatchCard batch={batch} onReview={onReview} />);
    const okTube = screen
      .getAllByTestId("batch-tube")
      .find((el) => el.getAttribute("data-verdict") === "OK")!;
    fireEvent.click(okTube);
    expect(onReview).not.toHaveBeenCalled();
  });
});

describe("BatchCard — 다발 동시 절단(수십 개) 규모", () => {
  beforeEach(() => stubImageFetch());
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("임계 이하(20개)는 전량 타일을 그대로 보여준다", () => {
    const batch = groupFeed(makeBatch(20, { ngIdx: [2] }))[0];
    render(<BatchCard batch={batch} />);
    const grid = screen.getByTestId("batch-tube-grid");
    expect(grid).toHaveAttribute("data-compact", "no");
    expect(grid.querySelectorAll("li").length).toBe(20);
  });

  it("64개 배치는 NG 번호만 보여준다(전량 타일은 480px 화면을 넘긴다)", () => {
    const batch = groupFeed(makeBatch(64, { ngIdx: [5, 40] }))[0];
    render(<BatchCard batch={batch} />);

    const grid = screen.getByTestId("batch-tube-grid");
    expect(grid).toHaveAttribute("data-compact", "yes");
    // NG 2개만. 64개를 다 그리면 한 줄 7개 × 10줄이라 화면을 넘긴다.
    expect(grid.querySelectorAll("li").length).toBe(2);
    expect(screen.getByTestId("batch-compact-note")).toHaveTextContent("양품 62개");
    // 총 개수는 여전히 정확히 보고된다.
    expect(screen.getByTestId("batch-card")).toHaveTextContent("총 64개 검사");
  });

  it("NG 가 너무 많으면 타일을 접고 남은 수를 알려준다", () => {
    const ngIdx = Array.from({ length: 40 }, (_, i) => i);
    const batch = groupFeed(makeBatch(64, { ngIdx }))[0];
    render(<BatchCard batch={batch} />);

    const grid = screen.getByTestId("batch-tube-grid");
    // 타일 24개 + "+16" 안내 1개.
    expect(screen.getByTestId("batch-tube-more")).toHaveTextContent("+16");
    expect(grid.querySelectorAll("li").length).toBe(25);
  });
});
