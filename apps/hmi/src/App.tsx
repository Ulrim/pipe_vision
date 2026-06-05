/**
 * AIVIS 작업자 HMI 메인 화면 (CLAUDE.md §5 M6/M10).
 * - WS /ws/live 구독(자동 재연결).
 * - 최신 검사결과 카드 + NG 알람 배너 + 최근 이력 + 재확인 다이얼로그.
 */
import { useState } from "react";
import type { InspectionResult } from "@aivis/shared-types";
import { useLiveSocket } from "@/hooks/useLiveSocket";
import { useLiveStore } from "@/store/liveStore";
import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { AlarmBanner } from "@/components/AlarmBanner";
import { InspectionCard } from "@/components/InspectionCard";
import { RecentFeed } from "@/components/RecentFeed";
import { ReviewDialog } from "@/components/ReviewDialog";

export default function App() {
  useLiveSocket();
  const latest = useLiveStore((s) => s.latest);
  const feed = useLiveStore((s) => s.feed);
  const [reviewing, setReviewing] = useState<InspectionResult | null>(null);

  return (
    <div className="min-h-full bg-gray-100 p-4 md:p-6">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-hmi-lg font-black text-gray-900">
          AIVIS 실시간 검사
        </h1>
        <ConnectionIndicator />
      </header>

      <div className="mb-4">
        <AlarmBanner />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <InspectionCard result={latest} onReview={setReviewing} />
        </div>
        <aside className="flex flex-col gap-3">
          <h2 className="text-hmi font-bold text-gray-700">최근 검사</h2>
          <RecentFeed feed={feed} onSelect={setReviewing} />
        </aside>
      </div>

      {reviewing && (
        <ReviewDialog
          result={reviewing}
          onClose={() => setReviewing(null)}
        />
      )}
    </div>
  );
}
