import { StateBlock } from "../../../../components/state-block";
import { fetchApiServer } from "../../../../lib/api/server";
import {
  buildEndaxisImportPayloadFromExport,
  MIN_SIMULATOR_PARSER_VERSION,
  parserVersionNumber,
  type BattleExportData,
} from "../../../../lib/endaxis-project";
import { EndaxisPreload } from "./endaxis-preload";
import { RotationsUnavailable } from "../../../../components/rotations-unavailable";
import { ROTATIONS_ENABLED } from "../../../../lib/feature-flags";

type AxisEditorPageProps = {
  params: Promise<{ battleId: string }>;
};

export const dynamic = "force-dynamic";

export default async function BattleAxisEditorPage({ params }: AxisEditorPageProps) {
  if (!ROTATIONS_ENABLED) return <RotationsUnavailable />;
  const { battleId } = await params;

  // 版本门禁先行：模拟器需要完整数据（施法序列 + 技力回复信号，v34+）。导出 422（无 casts）
  // 或解析器版本过低，一律禁止打开——数据不全会给出不准的排轴/伤害结果。先于 detail 判定，
  // 让老战斗稳定得到清晰提示（而非被 detail 的排名门禁 404 兜成通用错误）。
  let battleExport: BattleExportData | null = null;
  try {
    battleExport = await fetchApiServer<BattleExportData>(`/api/v1/battles/${battleId}/export`);
  } catch {
    battleExport = null;
  }
  if (
    battleExport === null ||
    parserVersionNumber(battleExport.parserVersion) < MIN_SIMULATOR_PARSER_VERSION
  ) {
    return (
      <StateBlock
        title="该战斗无法打开模拟器"
        description="此战斗由较旧版本客户端记录，缺少模拟器所需的完整数据（施法序列与技力回复信号）。请更新客户端后重新记录战斗，再打开模拟器。"
      />
    );
  }

  try {
    // 只从导出构建（导出门禁 valid+public，玩家非最佳战斗也能开；不依赖排名门禁的 detail）。
    const payload = buildEndaxisImportPayloadFromExport(battleExport);
    return <EndaxisPreload payload={payload} />;
  } catch (error) {
    return (
      <StateBlock
        title="排轴编辑器暂时不可用"
        description={error instanceof Error ? error.message : "战斗数据加载失败。"}
      />
    );
  }
}

export const revalidate = 0;
