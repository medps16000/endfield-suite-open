"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { ContractTagSummary } from "../../components/contract-tag-summary";
import { CharacterAvatar } from "../../components/character-avatar";
import { buildApiUrl } from "../../lib/api/client";
import type { AuthUser, UserBattlesResponse } from "../../lib/api/types";
import { hasContractTagData } from "../../lib/contract-tags";
import { formatBossDisplayName } from "../../lib/format/boss-display";
import { formatDurationMs } from "../../lib/format/duration";
import { rankingGroups } from "./ranking-groups.generated";
import type { RankingBossEntry, RankingGroupKey } from "./ranking-groups.generated";
import { ROTATIONS_ENABLED } from "../../lib/feature-flags";

type UserBattlesDashboardProps = {
  battles: UserBattlesResponse["battles"];
  currentUser: AuthUser;
  rankings: UserBattlesResponse["rankings"];
};

type RankingRow = UserBattlesResponse["rankings"][number];

const numberFormatter = new Intl.NumberFormat("zh-CN");
const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return dateTimeFormatter.format(date);
}

function formatNumber(value: number) {
  return numberFormatter.format(Math.round(value));
}

function formatBossShortName(value: string) {
  return value.replace(/^(危境再现|危境碎片|危机合约|影拓丰碑\d期)[·・\s]*/, "");
}

function getRankingGroupKey(row: Pick<RankingRow, "bossSlug" | "dungeonName">): RankingGroupKey | null {
  return rankingGroups.find((group) => group.bosses.some((boss) => boss.slug === row.bossSlug))?.key ?? null;
}

function getInitialRankingGroup(rankings: RankingRow[]): RankingGroupKey {
  return rankingGroups.find((group) => rankings.some((row) => getRankingGroupKey(row) === group.key))?.key ?? "crisis";
}

function getScoreTone(scorePercent: number) {
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

function formatRankingSubtitle(boss: RankingBossEntry, ranking: RankingRow | null) {
  if (ranking) {
    const displayName = formatBossDisplayName(ranking);
    return displayName === ranking.dungeonName ? ranking.dungeonName : `${ranking.dungeonName} · ${formatBossShortName(displayName)}`;
  }
  if (boss.slug.startsWith("indie_hard")) {
    return `${formatBossShortName(boss.name)} · ${formatBossShortName(boss.name)}`;
  }
  const displayName = formatBossDisplayName({ bossSlug: boss.slug, bossName: boss.name, dungeonName: boss.dungeonName });
  return displayName === boss.dungeonName ? boss.dungeonName : `${boss.dungeonName} · ${formatBossShortName(displayName)}`;
}

type AccountRankingsPanelProps = {
  rankings: UserBattlesResponse["rankings"];
  title: string;
  note?: string;
};

export function AccountRankingsPanel({
  rankings,
  title,
  note = "按公开榜单当前规则排名统计。",
}: AccountRankingsPanelProps) {
  const [rankingGroup, setRankingGroup] = useState<RankingGroupKey>(() => getInitialRankingGroup(rankings));
  const rankingCounts = useMemo(() => {
    return rankingGroups.reduce<Record<RankingGroupKey, number>>(
      (counts, group) => {
        counts[group.key] = rankings.filter((row) => getRankingGroupKey(row) === group.key).length;
        return counts;
      },
      {} as Record<RankingGroupKey, number>,
    );
  }, [rankings]);
  const filteredRankings = useMemo(() => {
    return rankings.filter((row) => getRankingGroupKey(row) === rankingGroup);
  }, [rankingGroup, rankings]);
  const activeRankingGroup = rankingGroups.find((group) => group.key === rankingGroup) ?? rankingGroups[0];
  const rankingTableRows = useMemo(() => {
    const rankingsByBossSlug = new Map(filteredRankings.map((row) => [row.bossSlug, row]));
    return activeRankingGroup.bosses.map((boss) => ({
      boss,
      ranking: rankingsByBossSlug.get(boss.slug) ?? null,
    }));
  }, [activeRankingGroup, filteredRankings]);
  const showContractTags = rankingTableRows.some(
    ({ ranking }) => ranking && hasContractTagData(ranking.contractTagScore, ranking.contractTags),
  );
  const rankingStats = useMemo(() => {
    if (!filteredRankings.length) {
      return {
        averagePercent: 0,
        bestPercent: 0,
        bestRank: 0,
        bestBossName: "",
      };
    }
    const bestByPercent = [...filteredRankings].sort((left, right) => {
      if (right.scorePercent !== left.scorePercent) {
        return right.scorePercent - left.scorePercent;
      }
      return left.rank - right.rank;
    })[0];
    const averagePercent = Math.round(
      filteredRankings.reduce((sum, row) => sum + row.scorePercent, 0) / filteredRankings.length,
    );
    return {
      averagePercent,
      bestPercent: bestByPercent.scorePercent,
      bestRank: bestByPercent.rank,
      bestBossName: formatBossDisplayName(bestByPercent),
    };
  }, [filteredRankings]);

  return (
    <section className="panel">
      <div className="table-toolbar">
        <div>
          <div className="eyebrow">账号排名</div>
          <h2 style={{ margin: "6px 0 0" }}>{title}</h2>
        </div>
        <span className="muted">{note}</span>
      </div>

      <div className="account-ranking-stack">
        <div className="account-ranking-filter-grid" role="group" aria-label="首领排名活动筛选">
          {rankingGroups.map((group) => (
            <button
              aria-pressed={rankingGroup === group.key}
              className={`account-ranking-filter-button${rankingGroup === group.key ? " is-active" : ""}`}
              key={group.key}
              onClick={() => setRankingGroup(group.key)}
              type="button"
            >
              <strong>{group.label}</strong>
              <span>{group.note}</span>
              <small>{rankingCounts[group.key]} 个上榜</small>
            </button>
          ))}
        </div>

        <div className="account-ranking-overview">
          <div className="account-ranking-chart">
            <div className="account-ranking-axis-label">百分比</div>
            <div className="account-ranking-bars">
              {filteredRankings.map((row) => {
                const scoreTone = getScoreTone(row.scorePercent);
                return (
                  <div className="account-ranking-bar-column" key={`chart-${row.bossSlug}-${row.battleId}`}>
                    <div className="account-ranking-bar-track">
                      <div
                        className={`account-ranking-bar ${scoreTone}`}
                        style={{ height: `${Math.max(row.scorePercent, 4)}%` }}
                      >
                        <span className={`account-score ${scoreTone}`}>{row.scorePercent}</span>
                      </div>
                    </div>
                    <span className="account-ranking-bar-label">{formatBossShortName(formatBossDisplayName(row))}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <aside className="account-ranking-summary">
            <span className="muted">账号表现</span>
            <strong className={`account-score ${getScoreTone(rankingStats.averagePercent)}`}>
              {filteredRankings.length ? `${rankingStats.averagePercent}%` : "-"}
            </strong>
            <span>平均百分比</span>
            <dl>
              <div>
                <dt>最佳</dt>
                <dd className={`account-score ${getScoreTone(rankingStats.bestPercent)}`}>
                  {filteredRankings.length ? `${rankingStats.bestPercent}%` : "-"}
                </dd>
              </div>
              <div>
                <dt>最佳排名</dt>
                <dd className={`account-score ${getScoreTone(rankingStats.bestPercent)}`}>
                  {filteredRankings.length ? rankingStats.bestRank : "-"}
                </dd>
              </div>
              <div>
                <dt>上榜首领</dt>
                <dd>{filteredRankings.length}</dd>
              </div>
            </dl>
            <span className="muted">{rankingStats.bestBossName ? formatBossShortName(rankingStats.bestBossName) : "-"}</span>
          </aside>
        </div>

        <div className="table-wrap">
          <table className="data-table account-ranking-table">
            <thead>
              <tr>
                <th>首领</th>
                <th>排名</th>
                <th>百分比</th>
                {showContractTags ? <th>合约</th> : null}
                <th>队伍</th>
                <th>链接</th>
              </tr>
            </thead>
            <tbody>
              {rankingTableRows.map(({ boss, ranking }) => {
                const scoreTone = getScoreTone(ranking?.scorePercent ?? 0);
                return (
                  <tr key={boss.slug}>
                    <td>
                      <div style={{ display: "grid", gap: 4 }}>
                        <strong>
                          {formatBossShortName(
                            ranking
                              ? formatBossDisplayName(ranking)
                              : formatBossDisplayName({
                                  bossSlug: boss.slug,
                                  bossName: boss.name,
                                  dungeonName: boss.dungeonName,
                                }),
                          )}
                        </strong>
                        <span className="muted">{formatRankingSubtitle(boss, ranking)}</span>
                      </div>
                    </td>
                    <td>
                      {ranking ? <strong className={`account-score ${scoreTone}`}>{ranking.rank}</strong> : <span className="muted">-</span>}
                    </td>
                    <td>
                      {ranking ? (
                        <div style={{ display: "grid", gap: 4 }}>
                          <strong className={`account-score ${scoreTone}`}>{ranking.scorePercent}%</strong>
                          <span className="muted">{formatDurationMs(ranking.durationMs)}</span>
                        </div>
                      ) : (
                        <span className="muted">-</span>
                      )}
                    </td>
                    {showContractTags ? (
                      <td>
                        {ranking && hasContractTagData(ranking.contractTagScore, ranking.contractTags) ? (
                          <ContractTagSummary compact score={ranking.contractTagScore} tags={ranking.contractTags} />
                        ) : (
                          <span className="muted">-</span>
                        )}
                      </td>
                    ) : null}
                    <td>
                      {ranking?.rosterEntries?.length ? (
                        <div className="ranking-roster-grid">
                          {ranking.rosterEntries.map((entry, index) => (
                            <span className="ranking-roster-chip" key={`${entry.characterKey ?? entry.characterName}-${index}`}>
                              <CharacterAvatar name={entry.characterName} avatarUrl={entry.avatarUrl} size="sm"
                                progression={{ potential: entry.characterPotential, refinement: entry.weaponRefine }} />
                              <span>{entry.characterName}</span>
                            </span>
                          ))}
                        </div>
                      ) : ranking?.rosterSummary.join(" / ") || <span className="muted">-</span>}
                    </td>
                    <td>
                      {ranking ? (
                        <Link className="button-secondary" href={`/battle/${ranking.battleId}`}>
                          查看
                        </Link>
                      ) : (
                        <span className="muted">-</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

export function UserBattlesDashboard({ battles: initialBattles, currentUser, rankings: initialRankings }: UserBattlesDashboardProps) {
  const [battles, setBattles] = useState(initialBattles);
  const [rankings, setRankings] = useState(initialRankings);
  const [statusFilter, setStatusFilter] = useState<"all" | "valid" | "deleted">("valid");
  const [query, setQuery] = useState("");
  const [nickname, setNickname] = useState(currentUser.nickname);
  const [nicknameDraft, setNicknameDraft] = useState(currentUser.nickname);
  const [nicknamePending, setNicknamePending] = useState(false);
  const [pendingBattleId, setPendingBattleId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const normalizedQuery = query.trim().toLowerCase();
  const filteredBattles = useMemo(() => {
    return battles.filter((battle) => {
      if (statusFilter !== "all" && battle.status !== statusFilter) {
        return false;
      }
      if (!normalizedQuery) {
        return true;
      }
      return [
        battle.id,
        battle.bossName,
        formatBossDisplayName(battle),
        battle.dungeonName,
        battle.rosterSummary.join(" "),
        battle.parserVersion,
        battle.rulesVersion,
      ].some((value) => value.toLowerCase().includes(normalizedQuery));
    });
  }, [battles, normalizedQuery, statusFilter]);

  const validBattles = battles.filter((battle) => battle.status === "valid").length;
  const deletedBattles = battles.length - validBattles;
  const latestBattle = battles[0] ?? null;
  const showBattleContractTags = filteredBattles.some(
    (battle) => hasContractTagData(battle.contractTagScore, battle.contractTags),
  );

  async function handleDeleteBattle(battleId: string) {
    if (pendingBattleId) {
      return;
    }
    const target = battles.find((battle) => battle.id === battleId);
    if (!target || target.status === "deleted") {
      return;
    }
    if (!window.confirm(`确定要删除 ${formatBossDisplayName(target)} 这条战斗记录吗？`)) {
      return;
    }

    setPendingBattleId(battleId);
    setMessage(null);
    try {
      const response = await fetch(buildApiUrl(`/api/battles/${battleId}`), {
        method: "DELETE",
        credentials: "include",
      });
      const data = (await response.json()) as { error?: { message?: string } };
      if (!response.ok) {
        throw new Error(data.error?.message ?? "删除战斗记录失败。");
      }
      setBattles((current) =>
        current.map((battle) => (battle.id === battleId ? { ...battle, status: "deleted" } : battle)),
      );
      setRankings((current) => current.filter((row) => row.battleId !== battleId));
      setMessage(`已删除记录 ${battleId}。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除战斗记录失败。");
    } finally {
      setPendingBattleId(null);
    }
  }

  async function handleUpdateNickname() {
    const nextNickname = nicknameDraft.trim();
    if (!nextNickname || nicknamePending || nextNickname === nickname) {
      return;
    }

    setNicknamePending(true);
    setMessage(null);
    try {
      const response = await fetch(buildApiUrl("/api/auth/me/nickname"), {
        method: "PATCH",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ nickname: nextNickname }),
      });
      const data = (await response.json()) as {
        user?: AuthUser | null;
        error?: { message?: string };
      };
      if (!response.ok || !data.user) {
        throw new Error(data.error?.message ?? "修改昵称失败。");
      }
      setNickname(data.user.nickname);
      setNicknameDraft(data.user.nickname);
      setMessage("昵称已更新，榜单和战斗详情会显示新的昵称。");
      window.dispatchEvent(new CustomEvent("auth-nickname-updated", { detail: data.user.nickname }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "修改昵称失败。");
    } finally {
      setNicknamePending(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="panel panel-muted">
        <div className="section-heading">
          <div>
            <div className="eyebrow">个人管理</div>
            <h1>管理我的战斗记录</h1>
            <p className="muted" style={{ margin: "8px 0 0" }}>
              登录后可以直接查看自己上传过的 battle，并在这里删除不想保留的记录。
            </p>
          </div>
          <Link className="button-chip" href="/">
            返回首页
          </Link>
        </div>

        <div className="stat-grid stat-grid-4" style={{ marginTop: 16 }}>
          <div className="metric-card">
            <span className="metric-label">总记录</span>
            <strong>{battles.length}</strong>
            <span className="metric-note">当前账号下全部 battle</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">有效记录</span>
            <strong>{validBattles}</strong>
            <span className="metric-note">仍可进入详情页</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">已删除</span>
            <strong>{deletedBattles}</strong>
            <span className="metric-note">已从公开页面移除</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">最近上传</span>
            <strong>{latestBattle ? formatDateTime(latestBattle.battleEndAt) : "--"}</strong>
            <span className="metric-note">{latestBattle ? formatBossDisplayName(latestBattle) : "暂无记录"}</span>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="table-toolbar">
          <div>
            <div className="eyebrow">账号设置</div>
            <h2 style={{ margin: "6px 0 0" }}>修改公开昵称</h2>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <input
              className="field-input"
              maxLength={32}
              minLength={2}
              onChange={(event) => setNicknameDraft(event.target.value)}
              style={{ width: 240 }}
              value={nicknameDraft}
            />
            <button
              className="button-primary"
              disabled={nicknamePending || nicknameDraft.trim().length < 2 || nicknameDraft.trim() === nickname}
              onClick={handleUpdateNickname}
              type="button"
            >
              {nicknamePending ? "保存中..." : "保存昵称"}
            </button>
          </div>
        </div>
      </section>

      <AccountRankingsPanel rankings={rankings} title="我在各首领中的排名" />

      <section className="panel">
        <div className="table-toolbar">
          <div>
            <div className="eyebrow">记录列表</div>
            <h2 style={{ margin: "6px 0 0" }}>查看、筛选或删除自己的 battle</h2>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <input
              className="field-input"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索首领 / 副本 / battleId"
              style={{ width: 260 }}
              value={query}
            />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                className={`button-chip${statusFilter === "valid" ? " is-active" : ""}`}
                onClick={() => setStatusFilter("valid")}
                type="button"
              >
                有效
              </button>
              <button
                className={`button-chip${statusFilter === "deleted" ? " is-active" : ""}`}
                onClick={() => setStatusFilter("deleted")}
                type="button"
              >
                已删除
              </button>
              <button
                className={`button-chip${statusFilter === "all" ? " is-active" : ""}`}
                onClick={() => setStatusFilter("all")}
                type="button"
              >
                全部
              </button>
            </div>
          </div>
        </div>

        {message ? (
          <div className="panel-inset" style={{ marginBottom: 12 }}>
            <span className="muted">{message}</span>
          </div>
        ) : null}

        {filteredBattles.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>状态</th>
                  <th>首领</th>
                  <th>阵容</th>
                  {showBattleContractTags ? <th>合约</th> : null}
                  <th>结束时间</th>
                  <th>DPS</th>
                  <th>版本</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredBattles.map((battle) => (
                  <tr className={battle.status === "deleted" ? "admin-row-deleted" : undefined} key={battle.id}>
                    <td>
                      <span className={`status-pill ${battle.status === "valid" ? "is-valid" : "is-deleted"}`}>
                        {battle.status === "valid" ? "有效" : "已删除"}
                      </span>
                    </td>
                    <td>
                      <div style={{ display: "grid", gap: 4 }}>
                        <strong>{formatBossDisplayName(battle)}</strong>
                        <span className="muted">{battle.dungeonName}</span>
                        <span className="muted">{battle.id}</span>
                      </div>
                    </td>
                    <td>{battle.rosterSummary.join(" / ") || "--"}</td>
                    {showBattleContractTags ? (
                      <td>
                        {hasContractTagData(battle.contractTagScore, battle.contractTags) ? (
                          <ContractTagSummary compact score={battle.contractTagScore} tags={battle.contractTags} />
                        ) : (
                          <span className="muted">-</span>
                        )}
                      </td>
                    ) : null}
                    <td>
                      <div style={{ display: "grid", gap: 4 }}>
                        <span>{formatDateTime(battle.battleEndAt)}</span>
                        <span className="muted">{formatDurationMs(battle.durationMs)}</span>
                      </div>
                    </td>
                    <td>{formatNumber(battle.totalDps)}</td>
                    <td>
                      <div style={{ display: "grid", gap: 4 }}>
                        <span>{battle.parserVersion}</span>
                        <span className="muted">{battle.rulesVersion}</span>
                      </div>
                    </td>
                    <td>
                      <div className="admin-actions">
                        {battle.status === "valid" ? (
                          <>
                            <Link className="button-secondary" href={`/battle/${battle.id}`}>
                              查看
                            </Link>
                            {ROTATIONS_ENABLED ? <Link className="button-secondary" href={`/axis/${battle.id}/editor`}>
                              排轴
                            </Link> : null}
                          </>
                        ) : null}
                        <button
                          className="button-danger"
                          disabled={battle.status === "deleted" || pendingBattleId === battle.id}
                          onClick={() => handleDeleteBattle(battle.id)}
                          type="button"
                        >
                          {pendingBattleId === battle.id ? "删除中..." : "删除"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state">
            {battles.length
              ? "当前筛选条件下没有符合的记录。"
              : "这个账号还没有上传过 battle，先用上传器传一条记录就会出现在这里。"}
          </div>
        )}
      </section>
    </div>
  );
}
