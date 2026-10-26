"use client";

/* eslint-disable @next/next/no-img-element -- Display original small game sprites and full artwork without image proxying. */

import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useRef, useState, type CSSProperties } from "react";
import type { CharacterProfile, ProfileCount } from "../lib/api/character-profile";
import { characterAvatars as avatarsManifest, officialIcons as iconManifest } from "../lib/generated/runtime-resources";
import type { DirectoryCharacter } from "./character-directory";
import styles from "./character-profile.module.css";
import { CharacterBossGrid } from "./character-boss-grid";

const avatars: Record<string, { url: string }> = avatarsManifest.avatars;
const icons: Record<string, { url: string; width: number; height: number; viewBox: string; color?: string }> = iconManifest.icons;
const ranges = [{ value: "all", name: "全部时间" }, { value: "30d", name: "最近 30 天" }, { value: "14d", name: "最近 14 天" }, { value: "7d", name: "最近 7 天" }];
const percent = (value: number) => `${value.toFixed(value > 0 && value < 1 ? 2 : 1)}%`;

function Distribution({ rows, total }: { rows: ProfileCount[]; total: number }) {
  return <div className={styles.distribution}>{rows.map((row) => <div className={styles.distributionRow} key={row.key} data-unknown={row.key === "unknown"}>
    <span>{row.name}</span><div className={styles.track}><i style={{ width: `${row.percent}%` }} /></div>
    <strong>{total ? percent(row.percent) : "—"}</strong><small>{row.count} 场</small>
  </div>)}</div>;
}

function UsageList({ rows, kind, total, startIndex = 0 }: { rows: ProfileCount[]; kind: "teammate" | "item"; total: number; startIndex?: number }) {
  if (!rows.length) return <p className={styles.noData}>{total ? "这批记录暂无可用数据" : "暂无符合条件的榜单记录"}</p>;
  return <div className={styles.usageList}>{rows.map((row, index) => {
    const icon = kind === "teammate" ? avatars[row.key]?.url : row.iconUrl;
    const content = <><span className={styles.usageRank}>{String(startIndex + index + 1).padStart(2, "0")}</span>
      {icon ? <Image src={icon} alt="" width={35} height={35} unoptimized={!icon.startsWith("/images/")} /> : <span className={styles.unknownIcon}>?</span>}
      <div className={styles.usageInfo}><strong>{row.name}</strong><div className={styles.usageTrack}><i style={{ width: `${row.percent}%` }} /></div></div>
      <div className={styles.usageValue}><strong>{percent(row.percent)}</strong><small>{row.count} 场</small></div></>;
    return kind === "teammate" ? <Link href={`/character/${row.key}`} className={styles.usageRow} key={row.key}>{content}</Link> : <div className={styles.usageRow} key={row.key}>{content}</div>;
  })}</div>;
}

export function CharacterProfileView({ character, profile, error }: { character: DirectoryCharacter; profile: CharacterProfile | null; error?: string }) {
  const router = useRouter();
  const fullArt = useRef<HTMLDialogElement>(null);
  const [loadedArt, setLoadedArt] = useState<string | null>(null);
  const changeFilter = (key: string, value: string) => {
    const params = new URLSearchParams();
    const range = key === "range" ? value : profile?.range ?? "all";
    const boss = key === "boss" ? value : profile?.bossSlug ?? "";
    if (range !== "all") params.set("range", range);
    if (boss) params.set("boss", boss);
    router.push(`/character/${character.id}${params.size ? `?${params}` : ""}`, { scroll: false });
  };
  const total = profile?.sampleCount ?? 0;
  const selectedBoss = profile?.bossOptions.find((boss) => boss.bossSlug === profile.bossSlug);
  return <div className={styles.page} style={{ "--element-color": icons[character.element]?.color } as CSSProperties}>
    <div className={styles.breadcrumb}><Link href="/">角色档案</Link><span>/</span><span>{character.name}</span></div>
    <section className={styles.hero}>
      <Image className={styles.heroAvatar} width={92} height={92} src={character.avatarImage} alt={`${character.name}头像`} priority />
      <div className={styles.heroTitle}><span className={styles.eyebrow}>OPERATOR RECORDS</span><h1>{character.name}</h1>
        <div className={styles.traits}>{[character.element, character.profession].map((name) => { const icon = icons[name]; return <span key={name}>{icon && <i style={{ backgroundColor: icon.color }}><svg viewBox={icon.viewBox} preserveAspectRatio="xMidYMid meet" aria-hidden="true"><image href={icon.url} width={icon.width} height={icon.height} /></svg></i>}{name}</span>; })}<span className={styles.stars}>{character.rarity} ★</span></div>
      </div>
      {character.fullImage && <button className={styles.artButton} onClick={() => fullArt.current?.showModal()} type="button">查看完整立绘 ↗</button>}
    </section>
    <section className={styles.scope} aria-label="统计范围">
      <div className={styles.filterFields}><label>时间范围<select value={profile?.range ?? "all"} onChange={(event) => changeFilter("range", event.target.value)} disabled={!profile}>{ranges.map((range) => <option key={range.value} value={range.value}>{range.name}</option>)}</select></label>
        <label className={styles.bossFilter}>竞速副本<select value={profile?.bossSlug ?? ""} onChange={(event) => changeFilter("boss", event.target.value)} disabled={!profile}><option value="">全部竞速副本</option>{profile?.bossOptions.map((boss) => <option key={boss.bossSlug} value={boss.bossSlug}>{boss.dungeonName} · {boss.bossName}{boss.sampleCount ? `（${boss.sampleCount}）` : ""}</option>)}</select></label>
      </div>
      <p>仅统计包含{character.name}的公开有效通关记录，同一账号在同一副本难度下保留最快一场。</p>
    </section>
    {error || !profile ? <div className={styles.error} role="alert"><h2>暂时无法读取角色统计</h2><p>{error ?? "请稍后重试。"}</p><button type="button" onClick={() => router.refresh()}>重新加载</button></div> : <>
      <div className={styles.summary}><div><span>榜单样本</span><strong>{total.toLocaleString()}<small> 场</small></strong></div><div><span>参与账号</span><strong>{profile.accountCount.toLocaleString()}</strong></div><div><span>覆盖副本</span><strong>{profile.bossCount}</strong></div><p>{selectedBoss ? selectedBoss.bossName : "全部竞速副本"}<small>{profile.latestBattleAt ? `最近样本 ${new Date(profile.latestBattleAt).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" })}` : "等待首批有效记录"}</small></p></div>
      {!total && <div className={styles.empty}>当前范围暂无该角色的榜单样本，可以切换时间或副本范围。</div>}
      <CharacterBossGrid key={`${character.id}:${profile.range}:${profile.bossSlug ?? "all"}`} profile={profile} characterName={character.name} />
      <section className={styles.panel} aria-labelledby="progression-title"><div className={styles.panelHeading}><h2 id="progression-title">潜能与精炼</h2><span>榜单样本占比 · {total} 场</span></div>
        <div className={styles.progressionGrid}><div><h3>角色潜能</h3><Distribution rows={profile.potentials} total={total} /></div><div><h3>武器精炼</h3><Distribution rows={profile.refinements} total={total} /></div></div>
        <details className={styles.combinations}><summary>查看潜能 + 精炼组合<span>{profile.combinations.length} 种</span></summary><div>{profile.combinations.map((row) => <span key={row.key}><strong>{row.name}</strong><small>{percent(row.percent)} · {row.count} 场</small></span>)}</div></details>
      </section>
      <div className={styles.twoColumns}>
        <section className={styles.panel} aria-labelledby="teammates-title"><div className={styles.panelHeading}><h2 id="teammates-title">常见队友</h2><span>同场出现率</span></div><UsageList rows={profile.teammates.slice(0, 8)} total={total} kind="teammate" />{profile.teammates.length > 8 && <details className={styles.more}><summary>展开其余 {profile.teammates.length - 8} 位队友</summary><UsageList rows={profile.teammates.slice(8)} startIndex={8} total={total} kind="teammate" /></details>}</section>
        <section className={styles.panel} aria-labelledby="weapons-title"><div className={styles.panelHeading}><h2 id="weapons-title">常见武器</h2><span>本场携带率</span></div><UsageList rows={profile.weapons.slice(0, 8)} total={total} kind="item" />{profile.weapons.length > 8 && <details className={styles.more}><summary>展开其余武器</summary><UsageList rows={profile.weapons.slice(8)} startIndex={8} total={total} kind="item" /></details>}</section>
      </div>
      <section className={styles.panel} aria-labelledby="equipment-title"><div className={styles.panelHeading}><h2 id="equipment-title">常见装备</h2><span>同一部件每场只计一次</span></div><div className={styles.equipmentGrid}><UsageList rows={profile.equipment.slice(0, 12)} total={total} kind="item" /></div>
        {profile.equipment.length > 12 && <details className={styles.more}><summary>展开其余 {profile.equipment.length - 12} 件装备</summary><div className={styles.equipmentGrid}><UsageList rows={profile.equipment.slice(12)} startIndex={12} total={total} kind="item" /></div></details>}
        <p className={styles.note}>装备信息缺失或不完整：{profile.equipmentUnknownSamples} / {total} 场。占比以全部 {total} 场样本为分母。</p>
      </section>
      <details className={styles.method}><summary>统计口径</summary><p>本角色的四个模块共用同一批记录。先按角色和时间筛选，再按账号、具体副本及难度取最快一场；同一账号在不同副本可各计一场，因此这些比例代表榜单样本，不代表全体玩家。</p><p>角色竞速名次比较当前时间范围内，各角色参与队伍的最快通关时间；所有角色采用同样的有效记录条件，没有有效记录的角色不计入排名。用时相同则并列，后续名次按已排人数跳位（例如 1、1、3）。</p><p>采用记录本场的配装。缺失值标为未知，不用该账号其他战斗补齐。队友和装备按“有多少场带了它”计数，所以各项占比之和可以超过 100%。危机合约采用积分计榜，暂不计入本页纯竞速统计。</p><p>更新于 {new Date(profile.generatedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}。</p></details>
    </>}
    {character.fullImage && <dialog ref={fullArt} className={styles.artDialog} aria-label={`${character.name}完整立绘`} onClick={(event) => { if (event.target === event.currentTarget) fullArt.current?.close(); }}><button type="button" aria-label="关闭完整立绘" onClick={() => fullArt.current?.close()}>×</button>{loadedArt !== character.fullImage && <span className={styles.artLoading} role="status">正在加载完整立绘…</span>}<img loading="lazy" src={character.fullImage} alt={`${character.name}完整立绘`} onLoad={() => setLoadedArt(character.fullImage ?? null)} /></dialog>}
  </div>;
}
