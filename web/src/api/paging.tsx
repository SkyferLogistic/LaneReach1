/** Cursor pagination with "Load more" (the API returns `{items, next_cursor}`). */
import { useInfiniteQuery } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";
import { Button } from "../components/ui";

export interface CursorPage<T> {
  items: T[];
  next_cursor?: string | null;
}

export function usePaged<T>(queryKey: QueryKey, fetchPage: (cursor: string | undefined) => Promise<CursorPage<T>>) {
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return { ...query, items };
}

export function LoadMore({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
}: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
}) {
  if (!hasNextPage) return null;
  return (
    <div className="row" style={{ justifyContent: "center", padding: 12 }}>
      <Button busy={isFetchingNextPage} onClick={() => void fetchNextPage()}>
        Load more
      </Button>
    </div>
  );
}
