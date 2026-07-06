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
    // NG 요약 배너(색+아이콘+텍스트).
    expect(screen.getByTestId("batch-ng-summary")).toHaveTextContent(
      "총 4개 중 2개 NG",
    );
  });

  it("전체 OK 배치는 NG 요약 배너를 표시하지 않는다", () => {
    const batch = groupFeed(makeBatch(3, { ngIdx: [] }))[0];
    render(<BatchCard batch={batch} />);
    expect(screen.getByTestId("batch-card")).toHaveAttribute(
      "data-verdict",
      "OK",
    );
    expect(screen.queryByTestId("batch-ng-summary")).not.toBeInTheDocument();
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
