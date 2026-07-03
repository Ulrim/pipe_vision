import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("첫 페이지: 이전 비활성, 가득 차면 다음 활성", () => {
    render(<Pagination offset={0} limit={25} pageCount={25} onChange={() => {}} />);
    expect(screen.getByTestId("page-prev")).toBeDisabled();
    expect(screen.getByTestId("page-next")).not.toBeDisabled();
    expect(screen.getByTestId("page-range")).toHaveTextContent("1–25");
  });

  it("마지막(부분) 페이지: 다음 비활성", () => {
    render(<Pagination offset={50} limit={25} pageCount={10} onChange={() => {}} />);
    expect(screen.getByTestId("page-next")).toBeDisabled();
    expect(screen.getByTestId("page-prev")).not.toBeDisabled();
    expect(screen.getByTestId("page-range")).toHaveTextContent("51–60");
  });

  it("다음/이전 클릭 시 offset 이동", async () => {
    const onChange = vi.fn();
    render(<Pagination offset={25} limit={25} pageCount={25} onChange={onChange} />);
    await userEvent.click(screen.getByTestId("page-next"));
    expect(onChange).toHaveBeenCalledWith(50);
    await userEvent.click(screen.getByTestId("page-prev"));
    expect(onChange).toHaveBeenCalledWith(0);
  });
});
