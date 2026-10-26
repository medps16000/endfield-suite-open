import Link from "next/link";

import { CharacterAvatar } from "../../../components/character-avatar";
import { ContractTagSummary } from "../../../components/contract-tag-summary";
import { StateBlock } from "../../../components/state-block";
import { fetchApiServer } from "../../../lib/api/server";
import type { BossRankingResponse } from "../../../lib/api/types";
import { hasContractTagData } from "../../../lib/contract-tags";
import { formatBossDisplayName, formatBossEyebrow } from "../../../lib/format/boss-display";
import { formatDurationMs } from "../../../lib/format/duration";

type BossPageProps = {
  params: Promise<{ bossSlug: string }>;
  searchParams: Promise<{ metric?: "dps" | "rdps" }>;
};

const professionAbbreviationMap: Record<string, string> = {
  近卫: "近",
  重装: "重",
  辅助: "辅",
  突击: "突",
  术士: "术",
  先锋: "先",
  未归类: "?",
};

function getRankTone(scorePercent: number): string {
  if (scorePercent >= 100) {
    return "is-gold";
  }
  if (scorePercent >= 95) {
    return "is-orange";
  }
  if (scorePercent >= 75) {
    return "is-purple";
  }
  if (scorePercent >= 50) {
    return "is-blue";
  }
  if (scorePercent >= 25) {
    return "is-green";
  }
  return "is-gray";
}

function formatBattleTime(isoString: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(isoString));
}

function getBossDirectoryGroup(dungeonName: string): string {
  const shadowMonumentMatch = dungeonName.match(/^影拓丰碑\d+期/);
  if (shadowMonumentMatch) {
    return shadowMonumentMatch[0];
  }
  return dungeonName;
}

export const dynamic = "force-dynamic";

export default async function BossRankingPage({ params, searchParams }: BossPageProps) {
  const { bossSlug } = await params;
  const resolvedSearchParams = await searchParams;
  const metric = resolvedSearchParams.metric === "rdps" ? "rdps" : "dps";

  try {
    const [ranking, hotBosses] = await Promise.all([
      fetchApiServer<BossRankingResponse>(`/api/bosses/${bossSlug}/rankings?metric=${metric}`),
      fetchApiServer<
        {
          bossSlug: string;
          bossName: string;
          dungeonName: string;
        }[]
      >("/api/home/hot-bosses"),
    ]);
    const currentDirectoryGroup = getBossDirectoryGroup(ranking.dungeonName);
    const relatedBosses = hotBosses.filter((boss) => getBossDirectoryGroup(boss.dungeonName) === currentDirectoryGroup);
    const bossChips = relatedBosses.length > 0 ? relatedBosses : hotBosses;
    const showContractTags = ranking.rows.some((row) => hasContractTagData(row.contractTagScore, row.contractTags));
    const displayTitle = formatBossDisplayName(ranking);
    const displayEyebrow = formatBossEyebrow(ranking);
    const showCharacterStatistics = ranking.bossSlug !== "indie_group_ccdg";
    const rankingRuleCopy = showContractTags
      ? "公开榜单按合约分数优先排名，同分再比较通关时间。百分比和颜色显示当前榜单中的排名位置。"
      : "公开榜单按击杀时间排名，时间越短排名越高。百分比和颜色显示当前榜单中的排名位置。";

    return (
      <div className="page-stack">
        <section className="panel panel-muted boss-ranking-hero">
          <div className="breadcrumbs">首页 / 竞速榜单 / {displayTitle}</div>
          <div className="section-heading boss-ranking-hero-heading">
            <div>
              <div className="eyebrow">{displayEyebrow}</div>
              <h1>{displayTitle}</h1>
              <p className="muted boss-ranking-copy">{rankingRuleCopy}</p>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <Link
                className={metric === "dps" ? "button-primary" : "button-secondary"}
                href={`/boss/${bossSlug}?metric=dps`}
              >
                DPS
              </Link>
              <Link
                className={metric === "rdps" ? "button-primary" : "button-secondary"}
                href={`/boss/${bossSlug}?metric=rdps`}
              >
                rDPS
              </Link>
              {showCharacterStatistics ? (
                <Link
                  className="button-secondary"
                  href={`/boss/${bossSlug}/statistics?metric=${metric}&range=all`}
                >
                  角色统计
                </Link>
              ) : null}
            </div>
          </div>
          <div className="boss-chip-row">
            {bossChips.map((boss) => (
              <Link
                className={`button-chip${boss.bossSlug === ranking.bossSlug ? " is-active" : ""}`}
                href={`/boss/${boss.bossSlug}?metric=${metric}`}
                key={`chip-${boss.bossSlug}`}
              >
                {formatBossDisplayName(boss)}
              </Link>
            ))}
          </div>
        </section>

        <section className="panel profession-summary-panel">
          <div className="table-toolbar profession-summary-toolbar">
            <div>
              <div className="eyebrow">职业分组</div>
              <h2 style={{ margin: "6px 0 0" }}>成员构成总览</h2>
            </div>
            <div className="boss-filter-row">
              <span className="pill">当前口径：{metric === "dps" ? "DPS" : "rDPS"}</span>
              <span className="pill">上榜记录：{ranking.rows.length}</span>
            </div>
          </div>
          <div className="profession-summary-grid">
            {ranking.professionGroups.map((group) => (
              <section className="profession-group-card" key={group.profession}>
                <header className="profession-group-header">
                  <span
                    className="profession-badge"
                    data-profession={group.profession}
                    title={group.profession}
                  >
                    {professionAbbreviationMap[group.profession] ?? group.profession.slice(0, 1)}
                  </span>
                  <strong>{group.profession}</strong>
                </header>
                {group.entries.length > 0 ? (
                  <div className="profession-entry-list">
                    {group.entries.map((entry) => (
                      <div className="profession-entry" key={`${group.profession}-${entry.characterName}`}>
                        <span className="profession-entry-name character-inline">
                          <CharacterAvatar
                            avatarUrl={entry.avatarUrl}
                            name={entry.characterName}
                            size="sm"
                          />
                          <span>{entry.characterName}</span>
                        </span>
                        <span className="profession-entry-percent">{entry.usagePercent.toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="profession-empty">当前榜单里还没有这个职业的上榜角色。</div>
                )}
              </section>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="table-toolbar">
            <div>
              <div className="eyebrow">角色排名</div>
              <h2 style={{ margin: "6px 0 0" }}>公开榜单</h2>
            </div>
            <span className="muted">每个账号仅保留当前榜单规则下最好的一条有效通关记录。</span>
          </div>
          <div className="table-wrap">
            <table className="data-table ranking-report-table">
              <thead>
                <tr>
                  <th>排名</th>
                  <th>主C</th>
                  <th>账号</th>
                  <th>击杀时间</th>
                  {showContractTags ? <th>合约</th> : null}
                  <th>{metric === "dps" ? "总 DPS" : "总 rDPS"}</th>
                  <th>成员构成</th>
                  <th>时间</th>
                  <th>报告</th>
                </tr>
              </thead>
              <tbody>
                {ranking.rows.map((row) => {
                  const rankTone = getRankTone(row.scorePercent);
                  return (
                    <tr className={row.rank === 1 ? "is-featured" : undefined} key={row.battleId}>
                      <td className="ranking-rank-cell">
                        <div className={`ranking-rank-value ${rankTone}`}>{row.rank}</div>
                        <small className={`ranking-rank-percent ${rankTone}`}>{row.scorePercent}%</small>
                      </td>
                      <td>
                        <div className="ranking-character-cell">
                          <CharacterAvatar
                            avatarUrl={row.characterAvatarUrl}
                            name={row.characterName}
                            progression={{ potential: row.characterPotential, refinement: row.weaponRefine }}
                          />
                          <div>
                            <strong className="character-inline">
                              <span>{row.characterName}</span>
                              <span
                                className="profession-badge"
                                data-profession={row.characterProfession}
                                title={row.characterProfession}
                              >
                                {professionAbbreviationMap[row.characterProfession] ??
                                  row.characterProfession.slice(0, 1)}
                              </span>
                            </strong>
                            <small>{row.characterProfession}</small>
                          </div>
                        </div>
                      </td>
                      <td>
                        <Link
                          className="ranking-account-link"
                          href={`/records/${encodeURIComponent(row.accountId)}`}
                          title={`查看 ${row.accountDisplayName} 在各首领中的排名`}
                        >
                          {row.accountDisplayName}
                        </Link>
                      </td>
                      <td>{formatDurationMs(row.durationMs)}</td>
                      {showContractTags ? (
                        <td>
                          {hasContractTagData(row.contractTagScore, row.contractTags) ? (
                            <ContractTagSummary compact score={row.contractTagScore} tags={row.contractTags} />
                          ) : (
                            <span className="muted">-</span>
                          )}
                        </td>
                      ) : null}
                      <td>
                        <div className="ranking-metric-cell">
                          <strong>
                            {Math.round(metric === "dps" ? row.dps : row.rdps).toLocaleString()}
                          </strong>
                        </div>
                      </td>
                      <td>
                        <div className="ranking-roster-grid">
                          {row.rosterEntries.map((entry) => (
                            <span className="ranking-roster-chip" key={`${row.battleId}-${entry.characterName}`}>
                              <CharacterAvatar avatarUrl={entry.avatarUrl} name={entry.characterName} size="sm"
                                progression={{ potential: entry.characterPotential, refinement: entry.weaponRefine }} />
                              <span>{entry.characterName}</span>
                              <span
                                className="profession-badge"
                                data-profession={entry.profession}
                                title={entry.profession}
                              >
                                {professionAbbreviationMap[entry.profession] ?? entry.profession.slice(0, 1)}
                              </span>
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>{formatBattleTime(row.battleEndAt)}</td>
                      <td>
                        <Link className="button-chip" href={`/battle/${row.battleId}?metric=${metric}`}>
                          查看
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    );
  } catch (error) {
    return (
      <StateBlock
        title="竞速榜单暂时不可用"
        description={error instanceof Error ? error.message : "竞速榜单加载失败。"}
      />
    );
  }
}

export const revalidate = 0;
