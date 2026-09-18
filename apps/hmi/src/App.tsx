/**
 * AIVIS 작업자 HMI 메인 화면 (CLAUDE.md §5 M6/M10).
 *
 * **고정 화면 원칙(파이 7" 800x480 실측 후 재설계)**
 * 현장 작업자는 설비 앞에서 화면을 힐끗 볼 뿐, 스크롤하지 않는다(장갑도 낀다).
 * 그런데 이전 레이아웃은 800x480 에서 문서 높이가 1011px 이라 **531px 가 잘려**
 * 판정(OK/NG)조차 스크롤해야 보였다 — 검사 화면으로서 치명적이었다.
 * 그래서 화면을 `h-full overflow-hidden` 3단 고정 구조로 바꾼다:
 *
 *   [상단바 ~52px]  품목·LOT / 검출 / 상태 / 시각
 *   [본문  flex-1 ]  판정(초대형) | 판정 이미지     ← 남는 높이를 전부 차지
 *   [하단바 ~56px]  누적 카운터 + 최근 결과 타일
 *
 * 어떤 요소도 본문을 밀어내지 못하며(알람은 오버레이), 화면이 커지면 본문이
 * 늘어날 뿐 구조는 같다 — 파이 LCD 와 사무실 PC 가 같은 화면을 본다.
 * - WS /ws/live 구독(자동 재연결, ?token= 으로 JWT 인증).
 * - 미인증 시 LoginScreen 게이트.
 */
import { useState } from "react";
import type { InspectionResult } from "@aivis/shared-types";
import { useLiveSocket } from "@/hooks/useLiveSocket";
import { useLiveStore } from "@/store/liveStore";
import { useBatches } from "@/hooks/useBatches";
import { useAuthStore } from "@/store/authStore";
import { HmiHeader } from "@/components/HmiHeader";
import { AlarmBanner } from "@/components/AlarmBanner";
import { InspectionCard } from "@/components/InspectionCard";
import { BatchCard } from "@/components/BatchCard";
import { RecentFeed } from "@/components/RecentFeed";
import { ReviewDialog } from "@/components/ReviewDialog";
import { LoginScreen } from "@/components/LoginScreen";

export default function App() {
  // 전체 로그인 게이트: 세션(토큰)이 없으면 본문 대신 로그인 화면만 렌더.
  const session = useAuthStore((s) => s.session);
  if (!session) {
    return <LoginScreen />;
  }
  return <AppShell />;
}

function AppShell() {
  useLiveSocket();
  const latest = useLiveStore((s) => s.latest);
  // feed 를 배치 키(lot+inspected_at)로 그룹핑. 최신 배치가 맨 앞.
  const batches = useBatches();
  const latestBatch = batches[0] ?? null;
  const [reviewing, setReviewing] = useState<InspectionResult | null>(null);

  return (
    <div className="relative flex h-full flex-col overflow-hidden bg-gray-100">
      <HmiHeader />

      <main className="flex min-h-0 flex-1 flex-col p-2">
        {/* 최신 그룹이 다중 튜브 배치면 배치 화면, 아니면 단일(하위호환). */}
        {latestBatch?.isBatch ? (
          <BatchCard batch={latestBatch} onReview={setReviewing} />
        ) : (
          <InspectionCard result={latest} onReview={setReviewing} />
        )}
      </main>

      {/* 알람은 오버레이가 아니라 **흐름 안**에 둔다. 겹쳐 띄우면 판정과
          재확인 버튼을 가려버린다(800x480 실측 확인). 흐름에 두면 판정 영역이
          그만큼 줄 뿐 스크롤은 생기지 않는다. 평소에는 아무것도 렌더하지
          않으므로 화면을 차지하지도 않는다. */}
      <div className="flex-none px-2 pb-2 empty:hidden">
        <AlarmBanner />
      </div>

      <RecentFeed batches={batches} onSelect={setReviewing} />

      {reviewing && (
        <ReviewDialog result={reviewing} onClose={() => setReviewing(null)} />
      )}
    </div>
  );
}
