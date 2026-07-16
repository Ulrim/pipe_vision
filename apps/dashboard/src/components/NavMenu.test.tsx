import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { NavMenu } from "./NavMenu";

/** NavMenu 는 라우팅 컨텍스트만 필요 — App 전체보다 가벼운 단독 렌더로 검증. */
function renderNavMenu(route = "/kpi") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <NavMenu />
      <Routes>
        <Route path="/kpi" element={<div>KPI 화면</div>} />
        <Route path="/inspections" element={<div>검사이력 화면</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("NavMenu (평소엔 심플 — 메뉴 버튼만 노출, 클릭 시 펼침)", () => {
  it("초기 상태: 메뉴 버튼만 보이고 항목 목록은 숨겨져 있다", () => {
    renderNavMenu();
    expect(screen.getByTestId("nav-menu-button")).toBeInTheDocument();
    expect(screen.queryByTestId("nav-menu")).not.toBeInTheDocument();
    expect(screen.getByTestId("nav-menu-button")).toHaveAttribute("aria-expanded", "false");
  });

  it("메뉴 버튼 클릭 시 5개 항목이 나타난다", async () => {
    const user = userEvent.setup();
    renderNavMenu();
    await user.click(screen.getByTestId("nav-menu-button"));
    expect(screen.getByTestId("nav-menu")).toBeInTheDocument();
    expect(screen.getByTestId("nav-menu-button")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("menuitem")).toHaveLength(5);
    expect(screen.getByRole("menuitem", { name: "KPI" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "검사이력" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "불량통계" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "월간리포트" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "기준정보" })).toBeInTheDocument();
  });

  it("현재 활성 페이지 항목은 강조 스타일(bg-brand)이 적용된다", async () => {
    const user = userEvent.setup();
    renderNavMenu("/kpi");
    await user.click(screen.getByTestId("nav-menu-button"));
    expect(screen.getByRole("menuitem", { name: "KPI" }).className).toContain("bg-brand");
    expect(screen.getByRole("menuitem", { name: "검사이력" }).className).not.toContain("bg-brand");
  });

  it("항목 클릭 시 해당 페이지로 이동하고 메뉴가 자동으로 닫힌다", async () => {
    const user = userEvent.setup();
    renderNavMenu("/kpi");
    await user.click(screen.getByTestId("nav-menu-button"));
    await user.click(screen.getByRole("menuitem", { name: "검사이력" }));
    expect(await screen.findByText("검사이력 화면")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("nav-menu")).not.toBeInTheDocument());
  });

  it("메뉴가 열린 상태에서 바깥 영역 클릭 시 닫힌다", async () => {
    const user = userEvent.setup();
    renderNavMenu();
    await user.click(screen.getByTestId("nav-menu-button"));
    expect(screen.getByTestId("nav-menu")).toBeInTheDocument();
    await user.click(document.body);
    await waitFor(() => expect(screen.queryByTestId("nav-menu")).not.toBeInTheDocument());
  });

  it("메뉴가 열린 상태에서 Esc 키를 누르면 닫힌다", async () => {
    const user = userEvent.setup();
    renderNavMenu();
    await user.click(screen.getByTestId("nav-menu-button"));
    expect(screen.getByTestId("nav-menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("nav-menu")).not.toBeInTheDocument());
  });
});
