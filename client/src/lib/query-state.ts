import {
  functionalUpdate,
  type OnChangeFn,
  type SortingState,
} from "@tanstack/react-table";
import { useSearchParams } from "react-router-dom";

function parsedPage(value: string | null): number {
  if (value === null) {
    return 0;
  }
  const page = Number(value);
  return Number.isInteger(page) && page >= 1 ? page - 1 : 0;
}

export interface TableUrlState {
  search: string;
  sorting: SortingState;
  page: number;
  setSearch: (value: string) => void;
  setSorting: OnChangeFn<SortingState>;
  setPage: (value: number) => void;
}

export function useTableUrlState(): TableUrlState {
  const [searchParams, setSearchParams] = useSearchParams();
  const sortId = searchParams.get("sort");
  const sorting =
    sortId === null
      ? []
      : [
          {
            id: sortId,
            desc: searchParams.get("direction") === "desc",
          },
        ];

  const update = (changes: Record<string, string | undefined>) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value === undefined || value === "") {
          next.delete(key);
        } else {
          next.set(key, value);
        }
      }
      return next;
    });
  };

  return {
    search: searchParams.get("q") ?? "",
    sorting,
    page: parsedPage(searchParams.get("page")),
    setSearch: (value) => update({ q: value, page: undefined }),
    setSorting: (updater) => {
      const next = functionalUpdate(updater, sorting);
      const first = next[0];
      update({
        sort: first?.id,
        direction: first === undefined ? undefined : first.desc ? "desc" : "asc",
        page: undefined,
      });
    },
    setPage: (value) =>
      update({ page: value === 0 ? undefined : String(value + 1) }),
  };
}

export function useFilterParam(
  name: string,
): [string | undefined, (value: string | undefined) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  return [
    searchParams.get(name) ?? undefined,
    (value) =>
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        if (value === undefined || value === "") {
          next.delete(name);
        } else {
          next.set(name, value);
        }
        next.delete("page");
        return next;
      }),
  ];
}
