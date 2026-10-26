"use client";

import Link from "next/link";
import Image from "next/image";
import { useState, type CSSProperties } from "react";
import { officialIcons } from "../lib/generated/runtime-resources";
import styles from "./character-directory.module.css";
import { rankingGroups } from "../features/records/ranking-groups.generated";

export type DirectoryCharacter = {
  id: string; name: string; englishName: string; profession: string;
  element: string; rarity: number; avatarImage: string; fullImage?: string;
};

const professions = ["近卫", "突击", "先锋", "重装", "术师", "辅助"];
const elements = ["物理", "灼热", "寒冷", "电磁", "自然"];
const gameIcons: Record<string, { url: string; width: number; height: number; viewBox: string; color?: string }> = officialIcons.icons;
const elementColors: Record<string, string> = Object.fromEntries(
  elements.map((name) => [name, gameIcons[name]?.color ?? "#8194a3"]),
);
const quickLinks = [
  { name: "危境再现", href: "/boss/dung01_group_bossrush01", mark: "target" },
  { name: "危境碎片", href: "/boss/dung02_group_minibossrush01", mark: "fragment" },
  { name: "危机合约", href: "/boss/indie_group_ccdg", mark: "contract" },
  { name: "战争回响", href: "/boss/indie_battletower001_ex", mark: "echo" },
];
const monumentPhases = rankingGroups
  .filter((group) => group.key.startsWith("shadowPhase") && group.bosses.length > 0)
  .map((group) => ({
    name: group.label.replace(/^影拓丰碑(\d+)期$/, "第 $1 期"),
    href: `/boss/${group.bosses[0].slug}`,
  }));

const iconPaths: Record<string, string> = {
  target: "M12 3v4m0 10v4M3 12h4m10 0h4M8 8h8v8H8V8Z",
  fragment: "m12 2 9 10-9 10-9-10 9-10Zm0 5 4 5-4 5-4-5 4-5Z",
  contract: "M6 3h12v18H6V3Zm3 5h6m-6 4h6m-6 4h3",
  echo: "m5 4 7 4 7-4v12l-7 4-7-4V4Zm7 4v12",
  monument: "m12 2 5 6-3 2v6l4 5H6l4-5v-6L7 8l5-6Z",
};

function Mark({ name, className }: { name: string; className?: string }) {
  const icon = gameIcons[name];
  if (icon) return <span className={`${styles.gameMark} ${className ?? ""}`} style={icon.color ? { backgroundColor: icon.color } : undefined} aria-hidden="true">
    <svg viewBox={icon.viewBox} preserveAspectRatio="xMidYMid meet" focusable="false">
      <image href={icon.url} width={icon.width} height={icon.height} />
    </svg>
  </span>;
  if (!iconPaths[name]) return null;
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

export function CharacterDirectory({ characters }: { characters: DirectoryCharacter[] }) {
  const [profession, setProfession] = useState("");
  const [element, setElement] = useState("");
  const [query, setQuery] = useState("");
  const [monumentOpen, setMonumentOpen] = useState(false);
  const searchTerm = query.trim().toLocaleLowerCase();
  const filtered = characters.filter((character) =>
    (!profession || character.profession === profession) &&
    (!element || character.element === element) &&
    (!searchTerm || character.name.includes(searchTerm) || character.englishName.toLocaleLowerCase().includes(searchTerm)),
  );
  const groups = professions.map((name) => ({
    name,
    characters: filtered.filter((character) => character.profession === name)
      .sort((a, b) => b.rarity - a.rarity || a.id.localeCompare(b.id)),
  })).filter((group) => group.characters.length > 0);
  const hasFilters = Boolean(profession || element || query);
  const clearFilters = () => { setProfession(""); setElement(""); setQuery(""); };

  return (
    <div className={styles.page}>
      <section aria-label="快速入口">
        <div className={styles.sectionLabel}><span />快速入口<small>QUICK ACCESS</small></div>
        <div className={styles.quickGrid}>
          {quickLinks.map((entry) => <Link key={entry.name} href={entry.href} className={styles.quickLink}>
            <Mark name={entry.mark} /><span>{entry.name}</span><span className={styles.quickArrow} aria-hidden="true">↗</span>
          </Link>)}
          <button className={`${styles.quickLink} ${monumentOpen ? styles.quickActive : ""}`} type="button" aria-expanded={monumentOpen} aria-controls="monument-phases" onClick={() => setMonumentOpen(!monumentOpen)}>
            <Mark name="monument" /><span>影拓丰碑</span><span className={styles.quickArrow} aria-hidden="true">{monumentOpen ? "−" : "+"}</span>
          </button>
        </div>
        {monumentOpen && <div id="monument-phases" className={styles.phases}>
          <span>选择期次</span>{monumentPhases.map((phase) => <Link key={phase.name} href={phase.href}>{phase.name}<span aria-hidden="true">↗</span></Link>)}
        </div>}
      </section>

      <section className={styles.directory} aria-labelledby="character-directory-title">
        <header className={styles.heading}>
          <div><div className={styles.eyebrow}>OPERATOR ARCHIVE</div><h1 id="character-directory-title">角色档案<span>.</span></h1><p>查找角色，探索配装与竞速表现。</p></div>
          <div className={styles.searchBox}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>
            <input type="search" aria-label="搜索角色" placeholder="搜索角色" value={query} onChange={(event) => setQuery(event.target.value)} /><span>{characters.length}</span>
          </div>
        </header>
        <div className={styles.filters}>
          <div className={styles.filterRow} role="group" aria-label="职业分支">
            <span className={styles.filterLabel}>职业分支</span><div className={styles.filterOptions}>
              <button type="button" aria-pressed={!profession} onClick={() => setProfession("")} className={!profession ? styles.activeFilter : ""}>全部</button>
              {professions.map((name) => <button key={name} type="button" aria-pressed={profession === name} className={profession === name ? styles.activeFilter : ""} onClick={() => setProfession(profession === name ? "" : name)}><Mark name={name} />{name}</button>)}
            </div>
          </div>
          <div className={styles.filterRow} role="group" aria-label="属性">
            <span className={styles.filterLabel}>属性</span><div className={styles.filterOptions}>
              <button type="button" aria-pressed={!element} onClick={() => setElement("")} className={!element ? styles.activeFilter : ""}>全部</button>
              {elements.map((name) => <button key={name} type="button" aria-pressed={element === name} className={element === name ? styles.activeElement : ""} style={{ "--element-color": elementColors[name] } as CSSProperties} onClick={() => setElement(element === name ? "" : name)}><Mark name={name} />{name}</button>)}
            </div>
          </div>
        </div>
        <div className={styles.resultsBar}>
          <span role="status" aria-live="polite">{hasFilters ? "筛选结果" : "全部角色"}<strong>{filtered.length}</strong> 位</span>
          {hasFilters && <button type="button" onClick={clearFilters}>重置筛选 ↺</button>}
        </div>
        <div className={styles.groups}>
          {groups.map((group) => <section key={group.name} aria-label={`${group.name}角色`}>
            <div className={styles.groupHeading}><Mark name={group.name} /><h2>{group.name}</h2><span>{String(group.characters.length).padStart(2, "0")}</span><div /></div>
            <div className={styles.cards}>
              {group.characters.map((character) => <Link key={character.id} href={`/character/${character.id}`} className={styles.card} data-rarity={character.rarity} style={{ "--element-color": elementColors[character.element] } as CSSProperties} aria-label={`查看${character.name}角色统计，${character.profession}，${character.element}，${character.rarity}星`}>
                <Image className={styles.avatar} src={character.avatarImage} alt={`${character.name}头像`} width={42} height={42} loading="lazy" decoding="async" />
                <div className={styles.cardCaption}>
                  <strong>{character.name}</strong>
                  <span className={styles.rarity} aria-label={`${character.rarity}星`}>{character.rarity}<span aria-hidden="true">★</span></span>
                </div>
                <div className={styles.cardBadges}><span title={character.element}><Mark name={character.element} /></span><span title={character.profession}><Mark name={character.profession} /></span></div>
              </Link>)}
            </div>
          </section>)}
          {filtered.length === 0 && <div className={styles.emptyState}><Mark name="target" /><h2>暂无符合条件的角色</h2><p>试试其他职业或属性，或清除当前筛选。</p><button type="button" onClick={clearFilters}>清除筛选</button></div>}
        </div>
      </section>
      <footer className={styles.footer}><span>终末地战斗日志</span><div><span>重庆数赋智联科技有限公司</span><a href="https://beian.miit.gov.cn/" rel="noopener noreferrer nofollow" target="_blank">渝ICP备2025075658号</a></div></footer>
    </div>
  );
}
