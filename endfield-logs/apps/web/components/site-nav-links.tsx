"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AboutDialog } from "./about-dialog";
import { rankingGroups } from "../features/records/ranking-groups.generated";
import { ROTATIONS_ENABLED } from "../lib/feature-flags";

const NAV_ITEMS = [
  { href: "/", label: "首页", match: (pathname: string) => pathname === "/" },
  ...(ROTATIONS_ENABLED ? [
    { href: "/rotations", label: "排轴", match: (pathname: string) => pathname === "/rotations" },
  ] : []),
];

const CLIENT_DOWNLOAD_URL = "/downloads/EndfieldLogsClient_3.0.12_20261016.zip";

export function SiteStatus() {
  const pathname = usePathname();
  if (pathname === "/" || pathname.startsWith("/character/")) return null;
  return <div className="site-status-row"><span className="pill pill-accent">测试环境</span><span>站点当前仍为测试入口，但公开榜单、竞速记录与整场战斗报告只展示真实上传数据。</span></div>;
}

const RANKING_MENU_ITEMS = [
  {
    href: "/statistics",
    label: "角色统计总榜",
    match: (pathname: string) => pathname === "/statistics",
  },
  {
    href: "/boss/dung01_group_bossrush01",
    label: "危境再现",
    match: (pathname: string) =>
      pathname.startsWith("/boss/dung01_group_bossrush") || pathname.startsWith("/boss/dung02_group_bossrush"),
  },
  {
    href: "/boss/dung02_group_minibossrush01",
    label: "危境碎片",
    match: (pathname: string) => pathname.startsWith("/boss/dung02_group_minibossrush"),
  },
  {
    href: "/boss/indie_group_ccdg",
    label: "危机合约",
    match: (pathname: string) => pathname.startsWith("/boss/indie_group_ccdg"),
  },
  {
    href: "/boss/indie_battletower001_ex",
    label: "战争回响",
    match: (pathname: string) => /^\/boss\/indie_battletower0(?:0[1-9]|1[0-4])_ex$/.test(pathname),
  },
  ...rankingGroups
    .filter((group) => group.key.startsWith("shadowPhase") && group.bosses.length > 0)
    .map((group) => ({
      href: `/boss/${group.bosses[0].slug}`,
      label: group.label,
      match: (pathname: string) => group.bosses.some((boss) =>
        pathname === `/boss/${boss.slug}` || pathname === `/boss/${boss.slug}/statistics`),
    })),
];

export function SiteNavLinks() {
  const pathname = usePathname();
  const [rankingMenuOpen, setRankingMenuOpen] = useState(false);
  const rankingMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!rankingMenuOpen) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      if (!rankingMenuRef.current?.contains(event.target as Node)) {
        setRankingMenuOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setRankingMenuOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [rankingMenuOpen]);

  return (
    <>
      <AboutDialog />
      {NAV_ITEMS.map((item) => (
        <Link
          className={`nav-link${item.match(pathname) ? " is-active" : ""}`}
          href={item.href}
          key={item.href}
        >
          {item.label}
        </Link>
      ))}
      <a className="nav-link" href={CLIENT_DOWNLOAD_URL} download>
        下载客户端
      </a>
      <div className="ranking-menu" ref={rankingMenuRef}>
        <button
          aria-expanded={rankingMenuOpen}
          aria-haspopup="menu"
          className={`nav-link ranking-menu-trigger${pathname.startsWith("/boss/") || pathname === "/statistics" ? " is-active" : ""}${
            rankingMenuOpen ? " is-open" : ""
          }`}
          onClick={() => setRankingMenuOpen((current) => !current)}
          type="button"
        >
          <span>竞速榜单</span>
          <span className="ranking-menu-caret" aria-hidden="true">
            ▾
          </span>
        </button>
        {rankingMenuOpen ? (
          <div className="ranking-menu-panel">
            {RANKING_MENU_ITEMS.map((item) => (
              <Link
                className={`ranking-menu-item${item.match(pathname) ? " is-active" : ""}`}
                href={item.href}
                key={item.href}
                onClick={() => setRankingMenuOpen(false)}
              >
                {item.label}
              </Link>
            ))}
          </div>
        ) : null}
      </div>
    </>
  );
}
