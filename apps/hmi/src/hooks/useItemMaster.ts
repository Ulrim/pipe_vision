/**
 * 현재 품목의 기준정보(공차) 조회.
 *
 * 공차 밴드 게이지가 "허용 범위"를 그리려면 tol_plus/tol_minus 가 필요한데
 * 검사결과(InspectionResult)에는 없다. 품목이 바뀌기 전까지 고정값이라
 * 길게 캐시하고, 실패해도 화면이 멈추면 안 되므로 조용히 null 을 돌려준다
 * (게이지만 안 그려지고 숫자는 그대로 나온다).
 */
import { useQuery } from "@tanstack/react-query";
import type { ItemMaster } from "@aivis/shared-types";
import { fetchItem } from "@/api/client";

export function useItemMaster(itemCode: string | null | undefined): ItemMaster | null {
  const { data } = useQuery({
    queryKey: ["item-master", itemCode],
    queryFn: () => fetchItem(itemCode as string),
    enabled: Boolean(itemCode),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  return data ?? null;
}
