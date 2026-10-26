import { BattleDetailView } from "../../../features/battle-detail/battle-detail-view";
import { StateBlock } from "../../../components/state-block";
import { fetchApiServer } from "../../../lib/api/server";
import type { BattleDetailResponse } from "../../../lib/api/types";
import { RotationsUnavailable } from "../../../components/rotations-unavailable";
import { ROTATIONS_ENABLED } from "../../../lib/feature-flags";

type AxisPageProps = {
  params: Promise<{ battleId: string }>;
  searchParams: Promise<{ metric?: "dps" | "rdps" }>;
};

export const dynamic = "force-dynamic";

export default async function BattleAxisPage({ params, searchParams }: AxisPageProps) {
  if (!ROTATIONS_ENABLED) return <RotationsUnavailable />;
  const { battleId } = await params;
  const resolvedSearchParams = await searchParams;
  const metric = resolvedSearchParams.metric === "rdps" ? "rdps" : "dps";
  try {
    const detail = await fetchApiServer<BattleDetailResponse>(`/api/battles/${battleId}`);
    return <BattleDetailView axisOnly detail={detail} metric={metric} />;
  } catch (error) {
    return (
      <StateBlock
        title="排轴视图暂时不可用"
        description={error instanceof Error ? error.message : "战斗数据加载失败。"}
      />
    );
  }
}

export const revalidate = 0;
