/**
 * 서버 페이지네이션 컨트롤 (대용량 조회, M11).
 * 총 건수를 모르는 limit/offset 페이징 — 다음 페이지 유무는
 * "받은 행 수 == limit" 휴리스틱으로 추정한다.
 */
export interface PaginationProps {
  offset: number;
  limit: number;
  /** 현재 페이지에서 실제로 받은 행 수. */
  pageCount: number;
  onChange: (nextOffset: number) => void;
}

export function Pagination({
  offset,
  limit,
  pageCount,
  onChange,
}: PaginationProps): JSX.Element {
  const page = Math.floor(offset / limit) + 1;
  const hasPrev = offset > 0;
  const hasNext = pageCount === limit; // 가득 찼으면 다음이 있을 수 있음
  const start = pageCount === 0 ? 0 : offset + 1;
  const end = offset + pageCount;

  return (
    <div className="flex items-center justify-between text-sm text-slate-600">
      <span data-testid="page-range">
        {start}–{end} 행 (페이지 {page})
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn-ghost"
          disabled={!hasPrev}
          onClick={() => onChange(Math.max(0, offset - limit))}
          data-testid="page-prev"
        >
          이전
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={!hasNext}
          onClick={() => onChange(offset + limit)}
          data-testid="page-next"
        >
          다음
        </button>
      </div>
    </div>
  );
}
