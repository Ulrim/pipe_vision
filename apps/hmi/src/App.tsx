/**
 * AIVIS 작업자 HMI 메인 화면 (CLAUDE.md §5 M6/M10).
 * - 사내 도구 전환: 전체 로그인 게이트(미인증 시 진입 차단).
 *   토큰이 없으면 LoginScreen 만 렌더, 있을 때만 검사 화면을 렌더한다.
 *   로그아웃/토큰 만료(401·WS 1008)로 세션이 폐기되면 다시 로그인 화면으로 복귀.
 * - WS /ws/live 구독(자동 재연결, ?token= 으로 JWT 인증).
 * - 최신 검사결과 카드 + NG 알람 배너 + 최근 이력 + 재확인 다이얼로그.
 */
import { useState } from "react";
import type { InspectionResult } from "@aivis/shared-types";
import { useLiveSocket } from "@/hooks/useLiveSocket";
import { useLiveStore } from "@/store/liveStore";
import { useBatches } from "@/hooks/useBatches";
import { useAuthStore } from "@/store/authStore";
import { ConnectionIndicator } from "@/components/ConnectionIndicator";
import { AlarmBanner } from "@/components/AlarmBanner";
import { InspectionCard } from "@/components/InspectionCard";
import { BatchCard } from "@/components/BatchCard";
import { RecentFeed } from "@/components/RecentFeed";
import { ReviewDialog } from "@/components/ReviewDialog";
import { AuthStatus } from "@/components/AuthStatus";
import { LoginScreen } from "@/components/LoginScreen";

export default function App() {
  // 전체 로그인 게이트: 세션(토큰)이 없으면 본문 대신 로그인 화면만 렌더.
  // 로그인/로그아웃/만료 시 session 변화로 게이트가 자동 전환된다.
  const session = useAuthStore((s) => s.session);
  if (!session) {
    return <LoginScreen />;
  }
  return <AppShell />;
}

/**
 * 인증된 본문. 게이트를 통과(토큰 보유)했을 때만 마운트되므로,
 * useLiveSocket 은 항상 유효한 토큰으로 WS 에 연결한다.
 */
function AppShell() {
  useLiveSocket();
  const latest = useLiveStore((s) => s.latest);
  // feed 를 배치 키(lot+inspected_at)로 그룹핑. 최신 배치가 맨 앞.
  const batches = useBatches();
  const latestBatch = batches[0] ?? null;
  const [reviewing, setReviewing] = useState<InspectionResult | null>(null);

  return (
    <div className="min-h-full bg-gray-100 p-4 md:p-6">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-hmi-lg font-black text-gray-900">
          AIVIS 실시간 검사
        </h1>
        <div className="flex flex-wrap items-center gap-3">
          <ConnectionIndicator />
          <AuthStatus />
        </div>
      </header>

      <div className="mb-4">
        <AlarmBanner />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          {/* 최신 그룹이 다중 튜브 배치면 배치 카드, 아니면 기존 단일 카드(하위호환). */}
          {latestBatch?.isBatch ? (
            <BatchCard batch={latestBatch} onReview={setReviewing} />
          ) : (
            <InspectionCard result={latest} onReview={setReviewing} />
          )}
        </div>
        <aside className="flex flex-col gap-3">
          <h2 className="text-hmi font-bold text-gray-700">최근 검사</h2>
          <RecentFeed batches={batches} onSelect={setReviewing} />
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
