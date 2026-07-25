import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useFilterParam, useTableUrlState } from "./query-state";

function wrapper({ children }: { children: ReactNode }) {
  return (
    <MemoryRouter
      initialEntries={[
        "/players?q=Ada&sort=name&direction=desc&page=2&team=1",
      ]}
    >
      {children}
    </MemoryRouter>
  );
}

describe("URL-backed table state", () => {
  it("reads and writes search, sorting, page, and filter state", () => {
    const { result } = renderHook(
      () => {
        const state = useTableUrlState();
        const [team, setTeam] = useFilterParam("team");
        const location = useLocation();
        return { state, team, setTeam, search: location.search };
      },
      { wrapper },
    );
    expect(result.current.state.search).toBe("Ada");
    expect(result.current.state.sorting).toEqual([
      { id: "name", desc: true },
    ]);
    expect(result.current.state.page).toBe(1);
    expect(result.current.team).toBe("1");

    act(() => result.current.state.setSearch("Beth"));
    expect(result.current.search).toContain("q=Beth");
    expect(result.current.search).not.toContain("page=");

    act(() =>
      result.current.state.setSorting([{ id: "points", desc: false }]),
    );
    expect(result.current.search).toContain("sort=points");
    expect(result.current.search).toContain("direction=asc");

    act(() => result.current.state.setSorting((current) => [...current]));
    expect(result.current.search).toContain("sort=points");

    act(() => result.current.state.setSorting([]));
    expect(result.current.search).not.toContain("sort=");

    act(() => result.current.state.setPage(2));
    expect(result.current.search).toContain("page=3");
    act(() => result.current.state.setPage(0));
    expect(result.current.search).not.toContain("page=");

    act(() => result.current.setTeam(undefined));
    expect(result.current.search).not.toContain("team=");
    act(() => result.current.setTeam("2"));
    expect(result.current.search).toContain("team=2");
  });

  it("treats malformed page values as the first page", () => {
    const invalidWrapper = ({ children }: { children: ReactNode }) => (
      <MemoryRouter initialEntries={["/?page=-2"]}>{children}</MemoryRouter>
    );
    const { result } = renderHook(() => useTableUrlState(), {
      wrapper: invalidWrapper,
    });
    expect(result.current.page).toBe(0);
  });
});
