"use client";

/* eslint-disable @next/next/no-img-element -- Reuse the site's original game artwork and small avatar sprites. */

import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { CharacterProfile, ProfileBoss } from "../lib/api/character-profile";
import { characterAvatars as avatarManifest } from "../lib/generated/runtime-resources";
import styles from "./character-boss-grid.module.css";

const avatars: Record<string, { url: string }> = avatarManifest.avatars;
const categories = [
  { id: "all", name: "全部", image: null },
  { id: "reappearance", name: "危境再现", image: "/images/boss-bg/dung01_group_bossrush01.webp" },
  { id: "fragment", name: "危境碎片", image: "/images/boss-bg/dung02_group_minibossrush01.webp" },
  { id: "echo", name: "战争回响", image: "/images/boss-bg/indie_battletower001_ex.webp" },
  { id: "monument", name: "影拓丰碑", image: "/images/boss-bg/indie_hard022_s.webp" },
];
const pageSize = 16;
const duration = (value: number) => `${Math.floor(value / 60000)}:${String(Math.floor(value / 1000) % 60).padStart(2, "0")}.${String(value % 1000).padStart(3, "0")}`;
// Presentation grouping of published category labels; battle identities and
// ranking eligibility are unchanged and continue to come from the API.
const categoryOf = (boss: ProfileBoss) => ({ "危境再现": "reappearance", "危境碎片": "fragment", "战争回响": "echo" }[boss.dungeonName]
  ?? (boss.dungeonName.startsWith("影拓丰碑") ? "monument" : "other"));
const phaseOf = (boss: ProfileBoss) => boss.dungeonName.match(/^影拓丰碑(\d+)期/)?.[1] ?? "";
const shortName = (boss: ProfileBoss) => boss.bossName.replace(/^危境再现·/, "").replace(/·(?:残酷|苦难)$/, "");

export function CharacterBossGrid({ profile, characterName }: { profile: CharacterProfile; characterName: string }) {
  const router = useRouter();
  const [category, setCategory] = useState("all");
  const [phase, setPhase] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("stage");
  const [page, setPage] = useState(0);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const drawer = useRef<HTMLDialogElement>(null);
  const selected = profile.bosses.find((boss) => boss.bossSlug === selectedSlug);
  const [details, setDetails] = useState<Record<string, { stamp: string; boss: ProfileBoss }>>({});
  const [detailError, setDetailError] = useState(false);
  const [retry, setRetry] = useState(0);
  const cachedDetail = selected && details[selected.bossSlug];
  const detail = selected && (selected.characterRows?.length ? selected : cachedDetail?.stamp === profile.generatedAt ? cachedDetail.boss : undefined);
  const phases = [...new Set(profile.bossOptions.map(phaseOf).filter(Boolean))].sort((a, b) => Number(b) - Number(a));
  const stageOrder = new Map(profile.bossOptions.map((boss, index) => [boss.bossSlug, index]));
  const term = query.trim().toLocaleLowerCase();
  const filtered = profile.bosses.filter((boss) =>
    (category === "all" || categoryOf(boss) === category)
    && (category !== "monument" || !phase || phaseOf(boss) === phase)
    && (!term || `${boss.bossName} ${boss.dungeonName}`.toLocaleLowerCase().includes(term)),
  ).sort((a, b) => (sort === "rank" ? (a.characterRank ?? Infinity) - (b.characterRank ?? Infinity) : 0)
    || (stageOrder.get(a.bossSlug) ?? Infinity) - (stageOrder.get(b.bossSlug) ?? Infinity));
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, totalPages - 1);
  const visible = filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize);

  useEffect(() => {
    const dialog = drawer.current;
    if (selected && dialog && !dialog.open) dialog.showModal();
    if (!selected && dialog?.open) dialog.close();
  }, [selected]);

  useEffect(() => {
    if (!selected || selected.characterRows?.length || details[selected.bossSlug]?.stamp === profile.generatedAt) return;
    const controller = new AbortController();
    setDetailError(false);
    const params = new URLSearchParams({ range: profile.range, boss: selected.bossSlug });
    fetch(`/api/characters/${profile.characterKey}/profile?${params}`, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Profile details unavailable");
        const payload = await response.json() as CharacterProfile;
        if (controller.signal.aborted) return;
        const result = payload.bosses.find((boss) => boss.bossSlug === selected.bossSlug);
        if (payload.characterKey !== profile.characterKey) throw new Error("Profile details mismatch");
        if (payload.generatedAt !== profile.generatedAt) { router.refresh(); return; }
        if (!result) throw new Error("Profile details mismatch");
        setDetails((previous) => ({ ...previous, [selected.bossSlug]: { stamp: payload.generatedAt, boss: result } }));
      })
      .catch(() => { if (!controller.signal.aborted) setDetailError(true); });
    return () => controller.abort();
  }, [selected, details, profile.characterKey, profile.range, profile.generatedAt, retry, router]);

  return <section className={styles.panel} aria-labelledby="ranking-title">
    <div className={styles.heading}><div><span>RACE RANKINGS</span><h2 id="ranking-title">{characterName}在各副本中的排名</h2></div><p>按队伍最快通关时间排名</p></div>
    <div className={styles.categoryFilters} role="group" aria-label="竞速排名分类">
      {categories.map((item) => <button key={item.id} type="button" aria-pressed={category === item.id} onClick={() => {
        setCategory(item.id); setPage(0);
        if (item.id === "monument") {
          const scoped = profile.bossOptions.find((boss) => boss.bossSlug === profile.bossSlug);
          setPhase((scoped && phaseOf(scoped)) || phases[0] || "");
        }
      }}>
        <span className={styles.categoryArt}>{item.image ? <Image src={item.image} alt="" width={30} height={30} /> : <i className={styles.allIcon}><i /><i /><i /><i /></i>}</span>
        <span>{item.name}</span><small>{item.id === "all" ? profile.bosses.length : profile.bosses.filter((boss) => categoryOf(boss) === item.id).length}</small>
      </button>)}
    </div>
    {category === "monument" && phases.length > 0 && <div className={styles.phases} role="group" aria-label="影拓丰碑期次"><span>期次</span><button type="button" aria-pressed={!phase} onClick={() => { setPhase(""); setPage(0); }}>全部</button>{phases.map((value, index) => <button key={value} type="button" aria-pressed={phase === value} onClick={() => { setPhase(value); setPage(0); }}>第 {value} 期{index === 0 && <small>最新</small>}</button>)}</div>}
    <div className={styles.toolbar}><label className={styles.search}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></svg><input type="search" placeholder="搜索副本" aria-label="搜索竞速副本" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} /></label>
      <label className={styles.sort}>排序<select aria-label="竞速排名排序" value={sort} onChange={(event) => { setSort(event.target.value); setPage(0); }}><option value="stage">副本顺序</option><option value="rank">角色名次优先</option></select></label>
      <span className={styles.resultCount} role="status" aria-live="polite">{filtered.length} 个副本</span>
    </div>
    <div className={styles.iconGrid}>
      {visible.map((boss) => <button key={boss.bossSlug} type="button" className={styles.bossTile} onClick={() => { setDetailError(false); setSelectedSlug(boss.bossSlug); }} aria-haspopup="dialog" aria-controls="boss-comparison-drawer"
        aria-label={`${shortName(boss)}，角色第${boss.characterRank ?? "未知"}名，查看对比`} title={`${boss.dungeonName} · ${boss.bossName}`}>
        <span className={styles.bossArt}><Image src={`/images/boss-bg/${boss.bossSlug}.webp`} alt="" width={96} height={96} sizes="(max-width: 600px) 76px, 96px" />
          {phaseOf(boss) && <small className={styles.phaseBadge}>{phaseOf(boss)} 期</small>}
          <span className={styles.rankBadge} data-leading={boss.characterRank === 1}><span>第</span><strong>{boss.characterRank ?? "—"}</strong><span>名</span></span>
        </span><strong className={styles.bossName}>{shortName(boss)}</strong>
      </button>)}
    </div>
    {!filtered.length && <div className={styles.empty}><p>{profile.bosses.length ? "没有符合筛选条件的副本" : "当前范围暂无竞速记录"}</p>{profile.bosses.length > 0 && <button type="button" onClick={() => { setCategory("all"); setPhase(""); setQuery(""); setPage(0); }}>清除筛选</button>}</div>}
    <div className={styles.bottomBar}><p>同用时并列 · 点击图标查看全角色对比</p><div className={styles.pagination}><button type="button" aria-label="上一页副本" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>‹</button><span>{currentPage + 1} / {totalPages}</span><button type="button" aria-label="下一页副本" disabled={currentPage + 1 >= totalPages} onClick={() => setPage(currentPage + 1)}>›</button></div></div>

    <dialog ref={drawer} id="boss-comparison-drawer" className={styles.drawer} aria-labelledby="boss-drawer-title" onClose={() => setSelectedSlug(null)} onClick={(event) => { if (event.target === event.currentTarget) drawer.current?.close(); }}>
      {selected && <>
        <header className={styles.drawerHeader}><Image src={`/images/boss-bg/${selected.bossSlug}.webp`} alt="" width={64} height={58} /><div><small>{selected.dungeonName}</small><h2 id="boss-drawer-title">{selected.bossName}</h2></div><button type="button" aria-label="关闭副本对比" onClick={() => drawer.current?.close()}>×</button></header>
        <div className={styles.drawerBody}><div className={styles.ownResult}><div><small>{characterName} · 角色名次</small><strong>第 {selected.characterRank ?? "—"} 名</strong></div><div><small>最快通关</small><strong>{selected.bestDurationMs != null ? duration(selected.bestDurationMs) : selected.rows[0] ? duration(selected.rows[0].durationMs) : "—"}</strong></div><div><small>榜单样本</small><strong>{selected.sampleCount} <em>场</em></strong></div></div>
          <p className={styles.comparisonNote}>比较携带各角色队伍的最快成绩，相同用时并列。</p>
          <div className={styles.tableScroll}><table><thead><tr><th>名次</th><th>角色</th><th>最快通关</th><th>样本</th><th>报告</th></tr></thead><tbody>{(detail?.characterRows ?? []).map((row) => <tr key={row.characterKey} data-current={row.characterKey === profile.characterKey}>
            <td className={styles.rank}>#{row.rank}</td><td><Link className={styles.rankedCharacter} href={`/character/${row.characterKey}?${new URLSearchParams({ range: profile.range, boss: selected.bossSlug })}`} onClick={() => drawer.current?.close()} aria-current={row.characterKey === profile.characterKey ? "page" : undefined}>{avatars[row.characterKey] && <Image src={avatars[row.characterKey].url} alt="" width={28} height={28} />}<span>{row.characterName}</span>{row.characterKey === profile.characterKey && <small>当前</small>}</Link></td><td className={styles.time}>{duration(row.bestDurationMs)}</td><td>{row.sampleCount} 场</td><td><Link href={`/battle/${row.battleId}`}>查看 ↗</Link></td>
          </tr>)}</tbody></table></div>
          {!detail && (detailError ? <div role="alert" className={styles.detailStatus}>详情加载失败。<button type="button" onClick={() => setRetry((value) => value + 1)}>重试</button></div> : <p role="status" className={styles.comparisonNote}>正在加载角色对比…</p>)}
          {detail && <details className={styles.playerRecords}><summary>查看{characterName}的玩家记录（{selected.sampleCount} 场）</summary><div className={styles.tableScroll}><table><thead><tr><th>名次</th><th>账号</th><th>潜能 + 精炼</th><th>通关时间</th><th>报告</th></tr></thead><tbody>{detail.rows.map((row) => <tr key={row.battleId}><td className={styles.rank}>#{row.rank}</td><td><Link href={`/records/${row.accountId}`}>{row.accountDisplayName}</Link></td><td>{row.potential ?? "—"} + {row.refinement ?? "—"}</td><td className={styles.time}>{duration(row.durationMs)}</td><td><Link href={`/battle/${row.battleId}`}>查看 ↗</Link></td></tr>)}</tbody></table></div>{selected.sampleCount > detail.rows.length && <p className={styles.comparisonNote}>展示前 {detail.rows.length} 名，共 {selected.sampleCount} 条去重记录。</p>}</details>}
        </div>
      </>}
    </dialog>
  </section>;
}
