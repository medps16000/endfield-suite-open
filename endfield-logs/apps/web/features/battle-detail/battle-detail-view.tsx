"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { CharacterAvatar } from "../../components/character-avatar";
import { ContractTagSummary } from "../../components/contract-tag-summary";
import { CriticalLuckPanel } from "./critical-luck-panel";
import { buildApiUrl } from "../../lib/api/client";
import type { BattleDetailResponse } from "../../lib/api/types";
import { hasContractTagData } from "../../lib/contract-tags";
import { formatBossDisplayName, formatBossEyebrow } from "../../lib/format/boss-display";
import { formatDurationMs } from "../../lib/format/duration";
import { ROTATIONS_ENABLED } from "../../lib/feature-flags";
import {
  getBattleSkillCategory,
  getBattleSkillCategoryLabel,
  getPreferredBattleSkillDisplayName,
} from "../../lib/format/skill-display";

type BattleDetailViewProps = {
  detail: BattleDetailResponse;
  metric: "dps" | "rdps";
  /** 排轴分享模式：只保留战斗摘要、阵容筛选和轨道时间轴，隐藏占比/统计表等区块。 */
  axisOnly?: boolean;
};

const TIMELINE_MIN_ZOOM = 0.5;
const TIMELINE_DEFAULT_ZOOM = 1.5;
const TIMELINE_MAX_ZOOM = 14;
const DAMAGE_CURVE_HEIGHT = 190;
const CONTRIBUTION_COLORS = ["#61c4df", "#8ccc4b", "#f0c971", "#72b4ff", "#ca8fff", "#ff8b72", "#9aa7b8"] as const;
const DAMAGE_ELEMENT_COLORS: Record<string, string> = {
  fire: "#ff5f5f",
  cryst: "#63a9ff",
  pulse: "#f5cf4e",
  natural: "#74d66b",
  physical: "#9aa3ad",
  spell: "#b98cff",
  unknown: "#687385",
};
const CHARACTER_COLLISION_COLORS = ["#c084fc", "#f472b6", "#fb923c", "#2dd4bf", "#a78bfa", "#e879f9"] as const;
const SKILL_HIT_GROUP_GAP_MS = 1200;
const ULTIMATE_HIT_GROUP_GAP_MS = 5000;
const CURVE_BUCKET_MS = 1000;
const TIMELINE_NODE_MIN_WIDTH_PX = 42;
const TIMELINE_NODE_MAX_WIDTH_PX = 132;
const TIMELINE_NODE_COMPACT_MIN_WIDTH_PX = 34;
const TIMELINE_NODE_MIN_VISIBLE_WIDTH_PX = 18;
// 窄于此宽度的技能块不渲染文字（半个字比没有字更难读），信息走悬浮提示。
// 块有 0 10px 内边距 + 1px 边框，12px/800 字重的单个汉字实测需 ~24px 内容区，故取 48。
const TIMELINE_NODE_LABEL_MIN_WIDTH_PX = 48;
const BUFF_AXIS_MAX_ROWS = 20;
const BUFF_AXIS_ROW_HEIGHT_PX = 24;
const BUFF_AXIS_PADDING_Y_PX = 10;
const LANE_BUFF_ROW_HEIGHT_PX = 16;
const LANE_BUFF_MAX_ROWS = 3;
const LANE_BUFF_MERGE_GAP_MS = 400;
const LANE_BUFF_ROW_GAP_PX = 4;
// 覆盖接近整场的 buff 视为常驻（装备/武器被动层数），不占用轨道行位。
const LANE_BUFF_ALWAYS_ON_RATIO = 0.85;
const ENEMY_TRACK_HEIGHT_PX = 72;
const BUFF_AXIS_BAR_MIN_WIDTH_PX = 22;
const BUFF_AXIS_TEAM_MERGE_GAP_MS = 80;
const BUFF_ZONE_LABELS: Record<string, string> = {
  atk: "攻击",
  dmg_inc: "增伤",
  fragile: "脆弱",
  vuln_taken: "易伤",
  amp: "增幅",
  res: "减抗",
  combo: "连击",
  speedup: "加速",
  slow: "减速",
};
const PLAYER_BUFF_AXIS_ZONES = new Set(["atk", "dmg_inc", "amp", "combo"]);
const ENEMY_DEBUFF_AXIS_ZONES = new Set(["dmg_inc", "amp", "fragile", "vuln_taken", "res"]);
const TIMELINE_ZERO_SOURCE_LABELS: Record<string, string> = {
  official_timer_start: "官方开始",
  game_timer_start: "计时开始",
  timer_end_inferred: "结束反推",
  timer_elapsed_first_hit_inferred: "首伤反推",
  hit_hint_window: "日志锚点",
  party_action_window: "首个动作",
  party_boss_hit_window: "首个首领命中",
  party_hit_window: "首个角色命中",
  hit_window: "首个命中",
  battle_ready: "阵容就绪",
  game_timer: "计时窗口",
};
const DAMAGE_ELEMENT_LABELS: Record<string, string> = {
  physical: "物理",
  fire: "灼热",
  pulse: "电磁",
  cryst: "寒冷",
  crystal: "寒冷",
  natural: "自然",
  spell: "法术",
  unknown: "未识别",
};
const DAMAGE_ELEMENT_ALIASES: Record<string, string> = {
  physical: "physical",
  "物理": "physical",
  fire: "fire",
  "灼热": "fire",
  "火": "fire",
  cryst: "cryst",
  crystal: "cryst",
  "寒冷": "cryst",
  "冰": "cryst",
  pulse: "pulse",
  "电磁": "pulse",
  "电弧": "pulse",
  natural: "natural",
  "自然": "natural",
  spell: "spell",
  "法术": "spell",
};

function getTimelineZeroSourceLabel(source: string | null | undefined) {
  if (!source) {
    return "未记录";
  }
  return TIMELINE_ZERO_SOURCE_LABELS[source] ?? source;
}

type TimelineEvent = BattleDetailResponse["timelineEvents"][number];
type TimelineBuffEffect = NonNullable<TimelineEvent["effects"]>[number];
type BattleRosterEquip = BattleDetailResponse["battle"]["roster"][number]["equips"][number];

const BASE_CHARACTER_ATTACK_RE = /^chr_\d{4}_[a-z0-9]+_attack_?\d+(?:$|_)/i;
const ATTACK_SHAPE_RE = /(?:^|_)(?:power_attack|heavy_attack|plunging_attack)(?:$|_)/i;
const ULTIMATE_ATTACK_SHAPE_RE = /(?:_ult_attack|_ultimate_attack|_ultimate_skill)/i;

type BuffAxisSegment = {
  name: string;
  eventKey: string | null;
  sourceText: string;
  targetText: string;
  startMs: number;
  endMs: number;
  actualStartMs: number;
  actualEndMs: number;
  effects: TimelineBuffEffect[];
  dynamicEffects: TimelineBuffEffect[];
};

type RawBuffAxisWindow = {
  groupKey: string;
  name: string;
  kind: "ally" | "enemy";
  startMs: number;
  endMs: number;
  sources: string[];
  targets: string[];
  actualStartMs: number;
  actualEndMs: number;
  segments: BuffAxisSegment[];
};

type BuffAxisWindow = {
  id: string;
  name: string;
  kind: "ally" | "enemy";
  startMs: number;
  endMs: number;
  durationMs: number;
  leftPx: number;
  widthPx: number;
  sources: string[];
  targets: string[];
  sourceText: string;
  targetText: string;
  actualStartMs: number;
  actualEndMs: number;
  segments: BuffAxisSegment[];
};

type SkillHoverState = {
  x: number;
  y: number;
  name: string;
  categoryLabel: string;
  tsMsFromStart: number;
  totalValue: number | null;
  hitCount: number;
  maxHit: number;
  damageElement: string | null;
  sourceName: string;
  targetName: string | null;
  eventKey: string | null;
};

type BuffHoverState = {
  buff: BuffAxisWindow;
  tsMsFromStart: number;
  x: number;
  y: number;
};

type ContributionChartEntry = {
  key: string;
  label: string;
  value: number;
  displayValue: string;
  color: string;
};

function clampTimelineZoom(nextZoom: number) {
  return Math.min(TIMELINE_MAX_ZOOM, Math.max(TIMELINE_MIN_ZOOM, nextZoom));
}

function formatTimelineOffsetMs(valueMs: number) {
  const sign = valueMs < 0 ? "-" : "";
  const absMs = Math.abs(valueMs);
  const totalSeconds = Math.floor(absMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const milliseconds = absMs % 1000;
  return `${sign}${minutes}:${seconds.toString().padStart(2, "0")}.${milliseconds.toString().padStart(3, "0")}`;
}

function getBuffAxisGroupEventKey(eventKey: string | null | undefined, name: string) {
  const key = eventKey?.trim();
  if (!key) {
    return name;
  }
  const affixMatch = key.match(
    /^(buff_common_affixes_(?:enhance|vulnerable))_(?:physical|fire|pulse|cryst|crystal|natural|spell)$/i,
  );
  if (affixMatch) {
    return affixMatch[1];
  }
  return key;
}

function isPlayerCharacterKey(value: string | null | undefined) {
  return Boolean(value?.trim().startsWith("chr_"));
}

function isEnemyKey(value: string | null | undefined) {
  return Boolean(value?.trim().startsWith("eny_"));
}

function getTimelineBuffTargetKeys(event: TimelineEvent) {
  const targetKey = event.targetCharacterKey?.trim() ?? "";
  return {
    player: event.targetPlayerKey?.trim() ?? (isPlayerCharacterKey(targetKey) ? targetKey : ""),
    enemy: event.targetEnemyKey?.trim() ?? (isEnemyKey(targetKey) ? targetKey : ""),
  };
}

function effectHasPositiveRate(effect: TimelineBuffEffect) {
  return [effect.rate, effect.baseRate, effect.tickRate, effect.maxRate].some(
    (value) => typeof value === "number" && Number.isFinite(value) && value > 0,
  );
}

function hasBuffAxisEffect(event: TimelineEvent, zones: Set<string>) {
  return [...(event.effects ?? []), ...(event.dynamicEffects ?? [])].some((effect) => {
    const zone = effect.zone?.trim() ?? "";
    return zones.has(zone) && effectHasPositiveRate(effect);
  });
}

function hasAnyBuffEffectDetail(event: TimelineEvent) {
  return (event.effects?.length ?? 0) > 0 || (event.dynamicEffects?.length ?? 0) > 0;
}

/** 同一次"全体施加"的识别键：同 buff、同一时刻（500ms 桶）、同时长的一组事件。 */
function getBuffApplicationIdentity(event: TimelineEvent) {
  return `${event.eventKey ?? event.eventName}|${Math.round(event.tsMsFromStart / 500)}|${Math.round(
    (event.durationMs ?? 0) / 500,
  )}`;
}

/** 每轨 buff 条的数值摘要：来自 effects/dynamicEffects（v31+ 上传才有），如"攻击 +5%，暴击伤害 +20%"。 */
function formatLaneBuffEffectText(event: TimelineEvent): string {
  const effects = [...(event.dynamicEffects?.length ? event.dynamicEffects : event.effects ?? [])];
  const lines = effects
    .map((effect) => {
      const rate = effect.rate ?? effect.baseRate;
      if (rate === null || rate === undefined || !Number.isFinite(rate)) {
        return "";
      }
      const zoneLabel = BUFF_ZONE_LABELS[effect.zone ?? ""] ?? (effect.zone || "效果");
      const element =
        effect.element && effect.element !== "all"
          ? `(${DAMAGE_ELEMENT_LABELS[effect.element] ?? effect.element})`
          : "";
      return `${zoneLabel}${element} +${formatBuffEffectRate(rate)}`;
    })
    .filter(Boolean);
  return Array.from(new Set(lines)).join("，");
}

/** 从 buff 事件 key 前缀推断来源类别，让排轴上能区分"这是什么 buff"。 */
function getBuffSourceCategory(eventKey: string | null | undefined): { label: string; className: string } {
  const key = (eventKey ?? "").toLowerCase();
  if (key.startsWith("buff_equipsuit_")) return { label: "套装", className: "is-cat-suit" };
  if (key.startsWith("buff_wpn_")) return { label: "武器", className: "is-cat-weapon" };
  if (key.startsWith("buff_chr_")) return { label: "技能", className: "is-cat-skill" };
  if (key.includes("potion") || key.includes("food")) return { label: "药剂", className: "is-cat-common" };
  if (key.includes("affixes")) return { label: "词缀", className: "is-cat-common" };
  if (key.startsWith("buff_common_")) return { label: "机制", className: "is-cat-common" };
  if (key.startsWith("buff_eny_")) return { label: "敌方", className: "is-cat-enemy" };
  return { label: "", className: "is-cat-other" };
}

/**
 * 装饰载体 buff：游戏用来播特效/挂图标/隐藏模型的附属 buff，会镜像真 buff 的数值造成重复显示。
 * akedata 全量模板审计（546 个）确认的装饰后缀：_vfx / _icon / _fx / _hide；
 * 新后缀变体由 infra/scripts/audit_buff_display_carriers.py 在版本更新时巡检（清单需两处同步）。
 */
function isCosmeticCarrierBuff(event: TimelineEvent) {
  return /_(vfx|icon|fx|hide)$/.test(event.eventKey ?? "");
}

function shouldShowBuffAxisEvent(event: TimelineEvent, rosterNames: Set<string>) {
  if (event.laneType !== "buff" || !event.durationMs || event.durationMs <= 0 || isCosmeticCarrierBuff(event)) {
    return false;
  }

  const sourceKey = event.sourceCharacterKey?.trim() ?? "";
  const targetKeys = getTimelineBuffTargetKeys(event);
  const sourceIsPlayer = isPlayerCharacterKey(sourceKey);
  const sourceIsEnemy = isEnemyKey(sourceKey);
  // 上传数据里 effects 词条和角色 key 都可能缺失（现网数据实测 key 全空、名字全填）。
  // 有词条时按 zone 白名单保精度；无词条时退回结构规则；key 缺失时再退回名字判定。
  const hasDetail = hasAnyBuffEffectDetail(event);

  if (isPlayerCharacterKey(targetKeys.player)) {
    if (!sourceIsPlayer || sourceKey === targetKeys.player) {
      return false;
    }
    return hasDetail ? hasBuffAxisEffect(event, PLAYER_BUFF_AXIS_ZONES) : true;
  }

  if (isEnemyKey(targetKeys.enemy)) {
    if (!sourceIsPlayer && !sourceIsEnemy) {
      return false;
    }
    return hasDetail ? hasBuffAxisEffect(event, ENEMY_DEBUFF_AXIS_ZONES) : true;
  }

  if (!sourceKey && !targetKeys.player && !targetKeys.enemy) {
    const sourceName = event.sourceCharacterName?.trim() ?? "";
    const targetName = event.targetCharacterName?.trim() ?? "";
    if (!sourceName || !targetName) {
      return false;
    }
    const sourceIsRoster = rosterNames.has(sourceName);
    const targetIsRoster = rosterNames.has(targetName);
    if (targetIsRoster) {
      if (!sourceIsRoster || sourceName === targetName) {
        return false;
      }
      return hasDetail ? hasBuffAxisEffect(event, PLAYER_BUFF_AXIS_ZONES) : true;
    }
    // 目标不在阵容里 => 视为敌方目标（boss 减抗/易伤窗口）。
    if (sourceIsRoster) {
      return hasDetail ? hasBuffAxisEffect(event, ENEMY_DEBUFF_AXIS_ZONES) : true;
    }
  }

  return false;
}

function formatCurveMetricValue(value: number) {
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: value > 0 ? 1 : 0,
    maximumFractionDigits: 1,
  });
}

function formatContributionPercent(value: number, total: number) {
  if (total <= 0) {
    return "0%";
  }
  const percent = (value / total) * 100;
  return `${percent >= 10 ? percent.toFixed(1) : percent.toFixed(2)}%`;
}

function formatSkillCategoryChartLabel(label: string) {
  return label === "连携" ? "连携技" : label;
}

function normalizeDamageElement(value: unknown) {
  const normalized = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (!normalized) {
    return "unknown";
  }
  return DAMAGE_ELEMENT_ALIASES[normalized] ?? normalized;
}

function getDamageElementColor(value: unknown) {
  const element = normalizeDamageElement(value);
  return DAMAGE_ELEMENT_COLORS[element] ?? DAMAGE_ELEMENT_COLORS.unknown;
}

function getNextCharacterCollisionColor(usedColors: Set<string>, assignedCount: number) {
  return (
    CHARACTER_COLLISION_COLORS.find((color) => !usedColors.has(color)) ??
    CHARACTER_COLLISION_COLORS[assignedCount % CHARACTER_COLLISION_COLORS.length]
  );
}

function getRecordString(payload: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function getTimelineEventDamageElement(event: TimelineEvent) {
  const direct = normalizeDamageElement(event.damageElement);
  if (direct !== "unknown") {
    return direct;
  }

  const hitContext = event.hitContext;
  if (hitContext && typeof hitContext === "object" && !Array.isArray(hitContext)) {
    const contextValue = getRecordString(hitContext as Record<string, unknown>, [
      "damageElement",
      "damage_element",
      "element",
    ]);
    const contextElement = normalizeDamageElement(contextValue);
    if (contextElement !== "unknown") {
      return contextElement;
    }
  }

  return "unknown";
}

function buildContributionEntries(
  rows: { key: string; label: string; value: number; displayValue: string }[],
  getColor?: (row: { key: string; label: string; value: number; displayValue: string }, index: number) => string,
) {
  return rows
    .filter((row) => Number.isFinite(row.value) && row.value > 0)
    .sort((left, right) => right.value - left.value || left.label.localeCompare(right.label, "zh-CN"))
    .map((row, index) => ({
      ...row,
      color: getColor?.(row, index) ?? CONTRIBUTION_COLORS[index % CONTRIBUTION_COLORS.length],
    }));
}

function ContributionDonutChart({
  title,
  subtitle,
  entries,
}: {
  title: string;
  subtitle: string;
  entries: ContributionChartEntry[];
}) {
  const total = entries.reduce((sum, entry) => sum + entry.value, 0);
  const dominantEntry = entries[0] ?? null;
  const radius = 45;
  const circumference = 2 * Math.PI * radius;
  let usedShare = 0;

  return (
    <article className="contribution-card">
      <header className="contribution-card-head">
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </header>
      {total > 0 ? (
        <div className="contribution-card-body">
          <div className="contribution-donut-wrap">
            <svg
              aria-label={title}
              className="contribution-donut"
              role="img"
              viewBox="0 0 120 120"
            >
              <circle className="contribution-donut-base" cx="60" cy="60" r={radius} />
              {entries.map((entry) => {
                const share = entry.value / total;
                const dash = Math.max(share * circumference - 1, 0);
                const offset = -usedShare * circumference;
                usedShare += share;
                return (
                  <circle
                    className="contribution-donut-segment"
                    cx="60"
                    cy="60"
                    key={entry.key}
                    r={radius}
                    stroke={entry.color}
                    strokeDasharray={`${dash} ${circumference}`}
                    strokeDashoffset={offset}
                  />
                );
              })}
            </svg>
            <div className="contribution-donut-center">
              <strong>{dominantEntry ? formatContributionPercent(dominantEntry.value, total) : "-"}</strong>
              <span>{dominantEntry?.label ?? "-"}</span>
            </div>
          </div>
          <div className="contribution-legend">
            {entries.map((entry) => (
              <div className="contribution-legend-row" key={`legend-${entry.key}`}>
                <span className="contribution-legend-name">
                  <span className="contribution-legend-dot" style={{ backgroundColor: entry.color }} />
                  <span>{entry.label}</span>
                </span>
                <span className="contribution-legend-value">
                  <strong>{formatContributionPercent(entry.value, total)}</strong>
                  <small>{entry.displayValue}</small>
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="empty-state">暂无可用数据。</div>
      )}
    </article>
  );
}

function estimateTimelineNodeWidthPx(label: string) {
  const visualUnits = Array.from(label).reduce((sum, char) => {
    return sum + (char.charCodeAt(0) > 255 ? 1 : 0.62);
  }, 0);
  return Math.max(
    TIMELINE_NODE_MIN_WIDTH_PX,
    Math.min(TIMELINE_NODE_MAX_WIDTH_PX, Math.round(20 + visualUnits * 14)),
  );
}

function normalizeTimelineName(value: string | null | undefined, fallback: string) {
  const normalized = value?.trim();
  return normalized && normalized.length > 0 ? normalized : fallback;
}

function formatNameList(names: string[], fallback: string) {
  const normalized = Array.from(new Set(names.map((name) => name.trim()).filter(Boolean))).sort((left, right) =>
    left.localeCompare(right, "zh-CN"),
  );
  if (normalized.length === 0) {
    return fallback;
  }
  if (normalized.length <= 2) {
    return normalized.join("、");
  }
  return `${normalized.slice(0, 2).join("、")} 等${normalized.length}个`;
}

function formatBuffEffectRate(rate: number | null | undefined) {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) {
    return "-";
  }
  const percent = rate * 100;
  const digits = Math.abs(percent) >= 10 ? 1 : 2;
  return `${percent.toFixed(digits).replace(/\.0+$/, "")}%`;
}

function formatSkillDamageShare(share: number) {
  if (!Number.isFinite(share) || share <= 0) {
    return "0%";
  }
  const percent = share * 100;
  if (percent < 0.1) {
    return "<0.1%";
  }
  return `${percent.toFixed(percent >= 10 ? 1 : 2).replace(/\.0+$/, "")}%`;
}

function hasPoiseDamage(event: TimelineEvent) {
  return event.poiseDamage?.type === "PoiseDamage";
}

function isPoiseHeavyBasicAttackEvent(event: TimelineEvent) {
  return hasPoiseDamage(event) && isBasicAttackShapeEvent(event) && !isUltimateAttackShapeEvent(event);
}

function isUltimateAttackShapeEvent(event: TimelineEvent) {
  const key = `${event.eventKey ?? ""} ${event.eventName ?? ""}`.toLowerCase();
  return ULTIMATE_ATTACK_SHAPE_RE.test(key);
}

function isBasicAttackShapeEvent(event: TimelineEvent) {
  const key = `${event.eventKey ?? ""}`.toLowerCase();
  return BASE_CHARACTER_ATTACK_RE.test(key) || ATTACK_SHAPE_RE.test(key);
}

function shouldRenderTimelineSkillNode(event: TimelineEvent) {
  if (event.laneType !== "skill") {
    return true;
  }
  if (isUltimateAttackShapeEvent(event)) {
    return true;
  }
  if (!isBasicAttackShapeEvent(event)) {
    return true;
  }
  return hasPoiseDamage(event);
}

function getAnalysisSkillCategoryLabel(event: TimelineEvent) {
  if (isPoiseHeavyBasicAttackEvent(event)) {
    return "重击";
  }
  const preferredName = getPreferredBattleSkillDisplayName(event.eventName, event.eventKey);
  return getBattleSkillCategoryLabel(preferredName, event.eventKey);
}

function getTimelineSkillHitTitle(
  event: TimelineEvent,
  skillCategoryLabel: string,
  preferredName: string,
  isPoiseHeavy: boolean,
) {
  const sourceTarget = `${event.sourceCharacterName ?? "未知"} -> ${event.targetCharacterName ?? "未知"}`;
  const damageText = (event.value ?? 0).toLocaleString();
  const poiseValue = event.poiseDamage?.value;
  const poiseText =
    isPoiseHeavy && typeof poiseValue === "number" && Number.isFinite(poiseValue)
      ? ` | 削韧 ${Math.abs(poiseValue).toLocaleString()}`
      : "";
  return `${skillCategoryLabel} | ${preferredName} | ${sourceTarget} | ${formatDurationMs(event.tsMsFromStart)} | ${damageText}${poiseText}`;
}

function getTimelineSkillMergeKey(event: TimelineEvent, displayName: string, skillCategory: ReturnType<typeof getBattleSkillCategory>) {
  if (skillCategory === "ultimate") {
    return [
      event.sourceCharacterKey ?? event.sourceCharacterName ?? "",
      event.targetCharacterKey ?? event.targetCharacterName ?? "",
      skillCategory,
      displayName,
    ].join("::");
  }
  return event.eventGroupKey ?? `${event.eventKey ?? event.eventName}::${event.targetCharacterName ?? ""}`;
}

function getTimelineSkillRenderPriority(skillCategory: ReturnType<typeof getBattleSkillCategory>) {
  switch (skillCategory) {
    case "other":
      return 0;
    case "normal":
      return 1;
    case "skill":
      return 2;
    case "combo":
      return 3;
    case "heavy":
      return 4;
    case "ultimate":
      return 5;
    default:
      return 1;
  }
}

function getTimelineSkillCompactLabel(
  skillCategory: ReturnType<typeof getBattleSkillCategory>,
  fallbackLabel: string,
) {
  switch (skillCategory) {
    case "normal":
      return "普";
    case "skill":
      return "战";
    case "combo":
      return "连";
    case "heavy":
      return "重";
    case "ultimate":
      return "终";
    default:
      return fallbackLabel.slice(0, 1) || "其";
  }
}

function canMergeTimelineSkillEvent(
  current: { _lastHitTsMs: number } | undefined,
  event: TimelineEvent,
  skillCategory: ReturnType<typeof getBattleSkillCategory>,
) {
  if (!current) {
    return false;
  }
  const gapMs = event.tsMsFromStart - current._lastHitTsMs;
  if (skillCategory === "ultimate") {
    return gapMs <= ULTIMATE_HIT_GROUP_GAP_MS;
  }
  if (event.eventGroupKey !== undefined && event.eventGroupKey !== null) {
    return true;
  }
  return gapMs <= SKILL_HIT_GROUP_GAP_MS;
}

function getBuffEffectRateAtTs(effect: TimelineBuffEffect, segment: BuffAxisSegment, tsMsFromStart: number) {
  if (effect.rate !== null && effect.rate !== undefined) {
    return effect.rate;
  }

  let rate = Math.max(0, effect.baseRate ?? 0);
  const tickRate = Math.max(0, effect.tickRate ?? 0);
  if (tickRate > 0) {
    const elapsedSeconds = Math.max(0, (tsMsFromStart - segment.actualStartMs) / 1000);
    rate += elapsedSeconds * tickRate;
  }

  const maxRate = Math.max(0, effect.maxRate ?? 0);
  if (maxRate > 0) {
    rate = Math.min(rate, maxRate);
  }
  return rate;
}

function formatBuffEffectLine(effect: TimelineBuffEffect, segment: BuffAxisSegment, tsMsFromStart: number) {
  const zone = effect.zone ?? "";
  const zoneLabel = BUFF_ZONE_LABELS[zone] ?? (zone || "效果");
  const element = effect.element && effect.element !== "all" ? `/${effect.element}` : "";
  const rate = getBuffEffectRateAtTs(effect, segment, tsMsFromStart);
  return `${zoneLabel}${element} ${formatBuffEffectRate(rate)}`;
}

function getBuffTooltipSegments(buff: BuffAxisWindow, tsMsFromStart: number) {
  return buff.segments.filter((segment) => segment.startMs <= tsMsFromStart && tsMsFromStart <= segment.endMs);
}

function getBuffSegmentEffectLines(segment: BuffAxisSegment, tsMsFromStart: number) {
  const effects = segment.dynamicEffects.length > 0 ? segment.dynamicEffects : segment.effects;
  const lines = effects.map((effect) => formatBuffEffectLine(effect, segment, tsMsFromStart));
  return Array.from(new Set(lines.filter((line) => !line.endsWith(" -"))));
}

function dedupeBuffTooltipSegments(segments: BuffAxisSegment[], tsMsFromStart: number) {
  const seen = new Set<string>();
  return segments.filter((segment) => {
    const effectKey = getBuffSegmentEffectLines(segment, tsMsFromStart).join("|");
    const key = `${segment.name}::${segment.sourceText}::${segment.startMs}::${segment.endMs}::${effectKey}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function LoadoutIcon({
  iconUrl,
  title,
  fallback,
  badge,
  isEmpty = false,
}: {
  iconUrl?: string | null;
  title: string;
  fallback: string;
  badge?: string | null;
  isEmpty?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const showImage = Boolean(iconUrl) && !failed && !isEmpty;

  return (
    <span className={`loadout-icon${isEmpty ? " is-empty" : ""}`} title={title}>
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          alt={title}
          className="loadout-icon-image"
          decoding="async"
          fetchPriority="low"
          loading="lazy"
          onError={() => setFailed(true)}
          src={iconUrl ?? undefined}
        />
      ) : (
        <span className="loadout-icon-fallback">{fallback}</span>
      )}
      {badge ? <span className="loadout-icon-badge">{badge}</span> : null}
    </span>
  );
}

function getDisplayEquipSlots(equips: BattleRosterEquip[]) {
  const slots: Array<BattleRosterEquip | null> = [null, null, null, null];
  const pending: BattleRosterEquip[] = [];
  const ordered = [...equips].sort((left, right) => left.slot - right.slot);
  const numericSlots = ordered.map((equip) => equip.slot);
  const looksOneBased =
    numericSlots.length === 4 &&
    !numericSlots.includes(0) &&
    numericSlots.every((slot) => Number.isInteger(slot) && slot >= 1 && slot <= 4);

  for (const equip of ordered) {
    const normalizedSlot = looksOneBased ? equip.slot - 1 : equip.slot;
    if (normalizedSlot >= 0 && normalizedSlot < slots.length && slots[normalizedSlot] === null) {
      slots[normalizedSlot] = equip;
    } else {
      pending.push(equip);
    }
  }

  for (const equip of pending) {
    const emptyIndex = slots.findIndex((slot) => slot === null);
    if (emptyIndex < 0) {
      break;
    }
    slots[emptyIndex] = equip;
  }

  return slots;
}

export function BattleDetailView({ detail, metric, axisOnly = false }: BattleDetailViewProps) {
  const router = useRouter();
  const timelineWrapRef = useRef<HTMLDivElement | null>(null);
  const [selectedCharacterKey, setSelectedCharacterKey] = useState<string | null>(null);
  const [deleteState, setDeleteState] = useState<"idle" | "submitting">("idle");
  const [deleteMessage, setDeleteMessage] = useState<string | null>(null);
  const [timelineZoom, setTimelineZoom] = useState(TIMELINE_DEFAULT_ZOOM);
  const [visibleCurveKeys, setVisibleCurveKeys] = useState<string[]>([]);
  const [curveHover, setCurveHover] = useState<{ x: number; tsMsFromStart: number } | null>(null);
  const [buffHover, setBuffHover] = useState<BuffHoverState | null>(null);
  const [skillHover, setSkillHover] = useState<SkillHoverState | null>(null);
  const [hoveredTimelineNodeKey, setHoveredTimelineNodeKey] = useState<string | null>(null);

  const rosterEntries = useMemo(() => {
    return detail.battle.roster.map((entry) => ({
      ...entry,
      filterKey: entry.characterKey ?? entry.characterName,
      participant: detail.participants.find((participant) => participant.characterName === entry.characterName) ?? null,
    }));
  }, [detail.battle.roster, detail.participants]);

  const characterElementByFilterKey = useMemo(() => {
    const result = new Map<string, string>();
    for (const entry of rosterEntries) {
      const directElement = normalizeDamageElement(entry.characterElement ?? entry.participant?.characterElement);
      if (directElement !== "unknown") {
        result.set(entry.filterKey, directElement);
        continue;
      }

      const totals = new Map<string, number>();
      for (const event of detail.timelineEvents) {
        if (event.laneType !== "skill" || event.sourceCharacterName !== entry.characterName || !event.value || event.value <= 0) {
          continue;
        }
        const element = getTimelineEventDamageElement(event);
        totals.set(element, (totals.get(element) ?? 0) + event.value);
      }
      const inferred = Array.from(totals.entries()).sort((left, right) => right[1] - left[1])[0]?.[0] ?? "unknown";
      result.set(entry.filterKey, inferred);
    }
    return result;
  }, [detail.timelineEvents, rosterEntries]);

  const characterColorByFilterKey = useMemo(() => {
    const result = new Map<string, string>();
    const elementCounts = new Map<string, number>();
    const usedColors = new Set<string>();
    for (const entry of rosterEntries) {
      const element = characterElementByFilterKey.get(entry.filterKey) ?? "unknown";
      const usedCount = elementCounts.get(element) ?? 0;
      const elementColor = getDamageElementColor(element);
      const color =
        usedCount === 0 && !usedColors.has(elementColor)
          ? elementColor
          : getNextCharacterCollisionColor(usedColors, result.size);
      result.set(entry.filterKey, color);
      usedColors.add(color);
      elementCounts.set(element, usedCount + 1);
    }
    return result;
  }, [characterElementByFilterKey, rosterEntries]);

  const selectedRosterEntry = useMemo(() => {
    if (!selectedCharacterKey) {
      return null;
    }
    return rosterEntries.find((entry) => entry.filterKey === selectedCharacterKey) ?? null;
  }, [rosterEntries, selectedCharacterKey]);

  const selectedCharacterName = selectedRosterEntry?.characterName ?? null;
  const curveMetricLabel = metric === "rdps" ? "rDPS" : "DPS";
  const battleTitle =
    isEnemyKey(detail.battle.bossName) && detail.battle.dungeonName
      ? detail.battle.dungeonName
      : formatBossDisplayName(detail.battle);
  const battleEyebrow = formatBossEyebrow(detail.battle);
  const battleMetricLabel = metric === "rdps" ? "总 rDPS" : "总 DPS";
  const battleMetricNote =
    metric === "rdps" ? "按当前 rDPS 归因口径汇总" : "当前战斗总输出速率";

  const battleMetricValue = useMemo(() => {
    if (metric === "rdps") {
      return detail.participants.reduce((sum, participant) => sum + participant.rdps, 0);
    }
    return detail.battle.totalDps;
  }, [detail.battle.totalDps, detail.participants, metric]);

  const dpsContributionEntries = useMemo(() => {
    return buildContributionEntries(
      detail.participants.map((participant) => ({
        key: participant.characterKey ?? participant.characterName,
        label: participant.characterName,
        value: participant.dps,
        displayValue: Math.round(participant.dps).toLocaleString("zh-CN"),
      })),
      (row) => characterColorByFilterKey.get(row.key) ?? getDamageElementColor("unknown"),
    );
  }, [characterColorByFilterKey, detail.participants]);

  const rdpsContributionEntries = useMemo(() => {
    return buildContributionEntries(
      detail.participants.map((participant) => ({
        key: participant.characterKey ?? participant.characterName,
        label: participant.characterName,
        value: participant.rdps,
        displayValue: Math.round(participant.rdps).toLocaleString("zh-CN"),
      })),
      (row) => characterColorByFilterKey.get(row.key) ?? getDamageElementColor("unknown"),
    );
  }, [characterColorByFilterKey, detail.participants]);

  const damageTypeContributionEntries = useMemo(() => {
    const totals = new Map<string, number>();
    for (const event of detail.timelineEvents) {
      if (event.laneType !== "skill" || !event.value || event.value <= 0) {
        continue;
      }
      const element = getTimelineEventDamageElement(event);
      totals.set(element, (totals.get(element) ?? 0) + event.value);
    }
    return buildContributionEntries(
      Array.from(totals.entries()).map(([element, value]) => ({
        key: element,
        label: DAMAGE_ELEMENT_LABELS[element] ?? element,
        value,
        displayValue: Math.round(value).toLocaleString("zh-CN"),
      })),
      (row) => getDamageElementColor(row.key),
    );
  }, [detail.timelineEvents]);

  const selectedCharacterDamageTypeContributionEntries = useMemo(() => {
    if (!selectedCharacterName) {
      return [];
    }
    const totals = new Map<string, number>();
    for (const event of detail.timelineEvents) {
      if (
        event.laneType !== "skill" ||
        event.sourceCharacterName !== selectedCharacterName ||
        !event.value ||
        event.value <= 0
      ) {
        continue;
      }
      const element = getTimelineEventDamageElement(event);
      totals.set(element, (totals.get(element) ?? 0) + event.value);
    }
    return buildContributionEntries(
      Array.from(totals.entries()).map(([element, value]) => ({
        key: element,
        label: DAMAGE_ELEMENT_LABELS[element] ?? element,
        value,
        displayValue: Math.round(value).toLocaleString("zh-CN"),
      })),
      (row) => getDamageElementColor(row.key),
    );
  }, [detail.timelineEvents, selectedCharacterName]);

  function applyTimelineZoom(nextZoom: number, anchorClientX?: number) {
    const node = timelineWrapRef.current;
    const currentZoom = timelineZoom;
    if (nextZoom === currentZoom) {
      return;
    }

    if (!node) {
      setTimelineZoom(nextZoom);
      return;
    }

    const rect = node.getBoundingClientRect();
    const anchorViewportX =
      anchorClientX !== undefined
        ? Math.min(Math.max(anchorClientX - rect.left, 0), rect.width)
        : rect.width / 2;
    const anchorContentX = node.scrollLeft + anchorViewportX;
    const zoomRatio = nextZoom / currentZoom;

    setTimelineZoom(nextZoom);

    requestAnimationFrame(() => {
      const updatedNode = timelineWrapRef.current;
      if (!updatedNode) {
        return;
      }
      const nextAnchorContentX = anchorContentX * zoomRatio;
      updatedNode.scrollLeft = Math.max(0, nextAnchorContentX - anchorViewportX);
    });
  }

  useEffect(() => {
    const node = timelineWrapRef.current;
    if (!node) {
      return;
    }

    const handleWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) {
        return;
      }
      event.preventDefault();
      const zoomFactor = Math.exp(-event.deltaY * 0.0015);
      applyTimelineZoom(clampTimelineZoom(timelineZoom * zoomFactor), event.clientX);
    };

    node.addEventListener("wheel", handleWheel, { passive: false });
    return () => {
      node.removeEventListener("wheel", handleWheel);
    };
  }, [timelineZoom]);

  const filteredTimeline = useMemo(() => {
    return detail.timelineEvents.filter((event) => {
      if (!selectedCharacterName) {
        return true;
      }
      return (
        event.sourceCharacterName === selectedCharacterName ||
        event.targetCharacterName === selectedCharacterName
      );
    });
  }, [detail.timelineEvents, selectedCharacterName]);

  const filteredParticipants = useMemo(() => {
    return detail.participants.filter((participant) => {
      if (!selectedCharacterName) {
        return true;
      }
      return participant.characterName === selectedCharacterName;
    });
  }, [detail.participants, selectedCharacterName]);

  const timelineLanes = useMemo(() => {
    return rosterEntries
      .filter((entry) => !selectedCharacterKey || entry.filterKey === selectedCharacterKey)
      .map((entry) => {
        const events = filteredTimeline
          .filter(
            (event) =>
              event.sourceCharacterName === entry.characterName &&
              event.laneType === "skill" &&
              shouldRenderTimelineSkillNode(event),
          )
          .sort((left, right) => left.tsMsFromStart - right.tsMsFromStart);

        const displayEvents: Array<
          (typeof events)[number] & {
            skillCategory: ReturnType<typeof getBattleSkillCategory>;
            skillCategoryLabel: string;
            hits: Array<{
              tsMsFromStart: number;
              value: number | null;
              title: string;
            }>;
            _lastHitTsMs: number;
          }
        > = [];
        const latestIndexByMergeKey = new Map<string, number>();

        for (const event of events) {
          const preferredName = getPreferredBattleSkillDisplayName(event.eventName, event.eventKey);
          const isPoiseHeavy = isPoiseHeavyBasicAttackEvent(event);
          const skillCategory: ReturnType<typeof getBattleSkillCategory> = isPoiseHeavy
            ? "heavy"
            : getBattleSkillCategory(preferredName, event.eventKey);
          const skillCategoryLabel = isPoiseHeavy
            ? "重击"
            : getBattleSkillCategoryLabel(preferredName, event.eventKey);
          const displayName = skillCategoryLabel;
          const hitTitle = getTimelineSkillHitTitle(event, skillCategoryLabel, preferredName, isPoiseHeavy);
          const mergeKey = getTimelineSkillMergeKey(event, displayName, skillCategory);
          const currentIndex = latestIndexByMergeKey.get(mergeKey);
          const current = currentIndex !== undefined ? displayEvents[currentIndex] : undefined;
          const canMerge = canMergeTimelineSkillEvent(current, event, skillCategory);

          if (!canMerge || !current) {
            displayEvents.push({
              ...event,
              eventName: displayName,
              skillCategory,
              skillCategoryLabel,
              value: event.value ?? 0,
              hits: [
                {
                  tsMsFromStart: event.tsMsFromStart,
                  value: event.value ?? 0,
                  title: hitTitle,
                },
              ],
              _lastHitTsMs: event.tsMsFromStart,
            });
            latestIndexByMergeKey.set(mergeKey, displayEvents.length - 1);
            continue;
          }

          current.value = (current.value ?? 0) + (event.value ?? 0);
          current.important = current.important || event.important;
          current._lastHitTsMs = event.tsMsFromStart;
          current.hits.push({
            tsMsFromStart: event.tsMsFromStart,
            value: event.value ?? 0,
            title: hitTitle,
          });

          const currentEnd = current.tsMsFromStart + (current.durationMs ?? 0);
          const eventEnd = event.tsMsFromStart + (event.durationMs ?? 0);
          const mergedDuration = Math.max(currentEnd, eventEnd) - current.tsMsFromStart;
          current.durationMs = mergedDuration > 0 ? mergedDuration : current.durationMs;
          latestIndexByMergeKey.set(mergeKey, currentIndex!);
        }

        displayEvents.sort((left, right) => {
          const leftFirstHitTs = left.hits[0]?.tsMsFromStart ?? left.tsMsFromStart;
          const rightFirstHitTs = right.hits[0]?.tsMsFromStart ?? right.tsMsFromStart;
          if (leftFirstHitTs !== rightFirstHitTs) {
            return leftFirstHitTs - rightFirstHitTs;
          }
          const priorityDiff =
            getTimelineSkillRenderPriority(left.skillCategory) -
            getTimelineSkillRenderPriority(right.skillCategory);
          if (priorityDiff !== 0) {
            return priorityDiff;
          }
          return (left.eventName ?? "").localeCompare(right.eventName ?? "", "zh-CN");
        });

        return {
          ...entry,
          events,
          displayEvents,
        };
      });
  }, [filteredTimeline, rosterEntries, selectedCharacterKey]);

  const selectedCharacterSkillTypeContributionEntries = useMemo(() => {
    if (!selectedCharacterName) {
      return [];
    }

    const totals = new Map<string, number>();
    for (const event of detail.timelineEvents) {
      if (
        event.laneType !== "skill" ||
        event.sourceCharacterName !== selectedCharacterName ||
        !event.value ||
        event.value <= 0
      ) {
        continue;
      }
      const label = formatSkillCategoryChartLabel(getAnalysisSkillCategoryLabel(event));
      totals.set(label, (totals.get(label) ?? 0) + event.value);
    }

    return buildContributionEntries(
      Array.from(totals.entries()).map(([label, value]) => ({
        key: label,
        label,
        value,
        displayValue: Math.round(value).toLocaleString("zh-CN"),
      })),
    );
  }, [detail.timelineEvents, selectedCharacterName]);

  const filteredSkills = useMemo(() => {
    const sourceStats =
      detail.roleSkillStats.length > 0
        ? detail.roleSkillStats
            .filter((stat) => !selectedCharacterName || stat.characterName === selectedCharacterName)
            .map((stat, index) => ({
              characterName: stat.characterName,
              skillKey: stat.skillKey ?? null,
              displayGroupKey: stat.displayGroupKey ?? (stat.skillKey ? `skill:${stat.skillKey}` : `row:${index}`),
              skillName: stat.displayGroupName ?? stat.skillName,
              skillCategoryLabel: getBattleSkillCategoryLabel(stat.skillName, stat.skillKey),
              hitCount: stat.hitCount ?? null,
              totalDamage: stat.totalDamage,
              maxHit: stat.maxHit ?? null,
            }))
        : detail.timelineEvents
            .filter(
              (event) =>
                event.laneType === "skill" &&
                (!selectedCharacterName || event.sourceCharacterName === selectedCharacterName) &&
                Boolean(event.sourceCharacterName) &&
                (event.value ?? 0) > 0,
            )
            .map((event, index) => {
              const skillName = getPreferredBattleSkillDisplayName(event.eventName, event.eventKey);
              const totalDamage = event.value ?? 0;
              return {
                characterName: event.sourceCharacterName ?? "未知",
                skillKey: event.eventKey ?? null,
                displayGroupKey: event.eventKey ? `skill:${event.eventKey}` : `event:${index}`,
                skillName,
                skillCategoryLabel: getAnalysisSkillCategoryLabel(event),
                hitCount: 1,
                totalDamage,
                maxHit: totalDamage,
              };
            });

    const groupedStats = new Map<string, (typeof sourceStats)[number]>();
    const characterTotals = new Map<string, number>();
    for (const stat of sourceStats) {
      characterTotals.set(
        stat.characterName,
        (characterTotals.get(stat.characterName) ?? 0) + stat.totalDamage,
      );
      // Official group identity joins damage branches. Unknown skills only join
      // their identical internal ID; labels and nearby timestamps are not proof.
      const groupKey = JSON.stringify([stat.characterName, stat.displayGroupKey]);
      const current = groupedStats.get(groupKey);
      if (!current) {
        groupedStats.set(groupKey, { ...stat });
        continue;
      }
      current.totalDamage += stat.totalDamage;
      current.hitCount = current.hitCount !== null && stat.hitCount !== null
        ? current.hitCount + stat.hitCount
        : null;
      current.maxHit = current.maxHit !== null && stat.maxHit !== null
        ? Math.max(current.maxHit, stat.maxHit)
        : null;
    }

    return Array.from(groupedStats.values())
      .map((stat) => ({
        characterName: stat.characterName,
        skillKey: stat.skillKey,
        skillName: stat.skillName,
        skillCategoryLabel: stat.skillCategoryLabel,
        hitCount: stat.hitCount,
        totalDamage: stat.totalDamage,
        damageShare: stat.totalDamage / Math.max(characterTotals.get(stat.characterName) ?? 0, 1),
        avgDamage: stat.hitCount !== null && stat.hitCount > 0 ? stat.totalDamage / stat.hitCount : null,
        maxHit: stat.maxHit,
      }))
      .sort((left, right) => {
        if (left.characterName !== right.characterName) {
          return left.characterName.localeCompare(right.characterName, "zh-CN");
        }
        const order = ["普攻", "战技", "连携", "重击", "终结技", "其他"];
        const leftIndex = order.indexOf(left.skillCategoryLabel);
        const rightIndex = order.indexOf(right.skillCategoryLabel);
        if (leftIndex !== rightIndex) {
          return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
        }
        return right.totalDamage - left.totalDamage;
      });
  }, [detail.roleSkillStats, detail.timelineEvents, selectedCharacterName]);

  const timelineMarks = useMemo(() => {
    const ratios = [0, 0.25, 0.5, 0.75, 1];
    return ratios.map((ratio) => ({
      label: formatDurationMs(Math.round(detail.battle.durationMs * ratio)),
      left: `${ratio * 100}%`,
    }));
  }, [detail.battle.durationMs]);

  const timelineBoardWidth = useMemo(() => {
    const minutes = Math.max(detail.battle.durationMs / 60000, 1);
    return Math.max(1320, Math.round(minutes * 1280 * timelineZoom));
  }, [detail.battle.durationMs, timelineZoom]);

  // 同一时刻覆盖全队的 buff（药剂、全体增益）：提升到顶部公共 Buff 轴，不在每条角色轨道重复。
  const teamWideBuffKeys = useMemo(() => {
    const rosterNames = new Set(
      rosterEntries.map((entry) => entry.characterName?.trim() ?? "").filter(Boolean),
    );
    const targetsByIdentity = new Map<string, Set<string>>();
    for (const event of detail.timelineEvents) {
      if (event.laneType !== "buff" || !event.durationMs || event.durationMs <= 0) {
        continue;
      }
      const targetName = event.targetCharacterName?.trim() ?? "";
      if (!targetName || !rosterNames.has(targetName)) {
        continue;
      }
      const identity = getBuffApplicationIdentity(event);
      const targets = targetsByIdentity.get(identity) ?? new Set<string>();
      targets.add(targetName);
      targetsByIdentity.set(identity, targets);
    }
    const keys = new Set<string>();
    if (rosterNames.size >= 2) {
      for (const [identity, targets] of targetsByIdentity) {
        if (targets.size >= rosterNames.size) {
          keys.add(identity);
        }
      }
    }
    return keys;
  }, [detail.timelineEvents, rosterEntries]);

  const laidOutBuffAxis = useMemo(() => {
    const battleDurationMs = Math.max(detail.battle.durationMs, 1);
    const rosterNames = new Set(
      rosterEntries.map((entry) => entry.characterName?.trim() ?? "").filter(Boolean),
    );
    const rawWindows: RawBuffAxisWindow[] = detail.timelineEvents
      .flatMap((event, index) => {
        if (isCosmeticCarrierBuff(event)) {
          return [];
        }
        const isTeamWide =
          event.laneType === "buff" &&
          (event.durationMs ?? 0) > 0 &&
          teamWideBuffKeys.has(getBuffApplicationIdentity(event));
        if (!isTeamWide && !shouldShowBuffAxisEvent(event, rosterNames)) {
          return [];
        }

        const sourceKey = event.sourceCharacterKey?.trim() ?? "";
        const targetKey = event.targetCharacterKey?.trim() ?? "";
        const sourceName = event.sourceCharacterName?.trim() ?? "";
        const targetName = event.targetCharacterName?.trim() ?? "";
        const durationMs = event.durationMs ?? 0;

        const actualStartMs = event.actualStartMsFromStart ?? event.tsMsFromStart;
        const actualEndMs = event.actualEndMsFromStart ?? event.tsMsFromStart + durationMs;
        const startMs = Math.min(Math.max(event.tsMsFromStart, 0), battleDurationMs);
        const endMs = Math.min(Math.max(event.tsMsFromStart + durationMs, startMs), battleDurationMs);
        if (endMs <= startMs) {
          return [];
        }

        const name = normalizeTimelineName(event.eventName, event.eventKey ?? "未知效果");
        const sources = sourceName ? [sourceName] : [];
        const targets = targetName ? [targetName] : [];
        const sourceText = formatNameList(sources, "未知来源");
        const targetText = formatNameList(targets, "未知目标");
        const targetKeys = getTimelineBuffTargetKeys(event);
        const kind: "ally" | "enemy" =
          !isTeamWide &&
          (isEnemyKey(targetKeys.enemy) || isEnemyKey(targetKey) || (targetName !== "" && !rosterNames.has(targetName)))
            ? "enemy"
            : "ally";

        return [{
          // 全体施加的 buff 合并为一条公共条（忽略来源差异，例如四人各自喝的同种药剂）。
          groupKey: `${getBuffAxisGroupEventKey(event.eventKey, name)}::${isTeamWide ? "team" : sourceKey || sourceName || "unknown-source"}`,
          name,
          kind,
          startMs,
          endMs,
          actualStartMs,
          actualEndMs,
          sources,
          targets,
          segments: [
            {
              name,
              eventKey: event.eventKey ?? null,
              sourceText,
              targetText,
              startMs,
              endMs,
              actualStartMs,
              actualEndMs,
              effects: event.effects ?? [],
              dynamicEffects: event.dynamicEffects ?? [],
            },
          ],
        }];
      })
      .sort((left, right) => {
        if (left.startMs !== right.startMs) {
          return left.startMs - right.startMs;
        }
        if (left.endMs !== right.endMs) {
          return right.endMs - left.endMs;
        }
        return left.name.localeCompare(right.name, "zh-CN");
      });

    const mergedWindows: RawBuffAxisWindow[] = [];
    const windowsByGroup = new Map<string, RawBuffAxisWindow[]>();
    for (const window of rawWindows) {
      const windows = windowsByGroup.get(window.groupKey) ?? [];
      windows.push(window);
      windowsByGroup.set(window.groupKey, windows);
    }

    for (const windows of windowsByGroup.values()) {
      let current: RawBuffAxisWindow | null = null;
      for (const window of windows) {
        if (!current || window.startMs > current.endMs + BUFF_AXIS_TEAM_MERGE_GAP_MS) {
          if (current) {
            mergedWindows.push(current);
          }
          current = {
            ...window,
            sources: [...window.sources],
            targets: [...window.targets],
            segments: [...window.segments],
          };
          continue;
        }

        current.startMs = Math.min(current.startMs, window.startMs);
        current.endMs = Math.max(current.endMs, window.endMs);
        current.actualStartMs = Math.min(current.actualStartMs, window.actualStartMs);
        current.actualEndMs = Math.max(current.actualEndMs, window.actualEndMs);
        current.sources = Array.from(new Set([...current.sources, ...window.sources]));
        current.targets = Array.from(new Set([...current.targets, ...window.targets]));
        current.segments.push(...window.segments);
      }
      if (current) {
        mergedWindows.push(current);
      }
    }

    const laidOutWindows = mergedWindows
      .map((window, index): BuffAxisWindow => {
        const leftPx = (window.startMs / battleDurationMs) * timelineBoardWidth;
        const widthPx = Math.max(
          ((window.endMs - window.startMs) / battleDurationMs) * timelineBoardWidth,
          BUFF_AXIS_BAR_MIN_WIDTH_PX,
        );
        const sources = [...window.sources].sort((left, right) => left.localeCompare(right, "zh-CN"));
        const targets = [...window.targets].sort((left, right) => left.localeCompare(right, "zh-CN"));

        return {
          id: `${window.groupKey}-${window.startMs}-${window.endMs}-${index}`,
          name: window.name,
          kind: window.kind,
          startMs: window.startMs,
          endMs: window.endMs,
          durationMs: window.endMs - window.startMs,
          leftPx,
          widthPx: Math.min(widthPx, Math.max(timelineBoardWidth - leftPx, BUFF_AXIS_BAR_MIN_WIDTH_PX)),
          sources,
          targets,
          sourceText: formatNameList(sources, "未知来源"),
          targetText: formatNameList(targets, "未知目标"),
          actualStartMs: window.actualStartMs,
          actualEndMs: window.actualEndMs,
          segments: window.segments.sort((left, right) => left.startMs - right.startMs),
        };
      })
      .sort((left, right) => {
        if (left.startMs !== right.startMs) {
          return left.startMs - right.startMs;
        }
        if (left.endMs !== right.endMs) {
          return right.endMs - left.endMs;
        }
        return left.name.localeCompare(right.name, "zh-CN");
      });

    const rows: BuffAxisWindow[][] = [];
    const rowRightEdges: number[] = [];
    let hiddenCount = 0;

    for (const window of laidOutWindows) {
      const visualRightEdge = window.leftPx + window.widthPx + 6;
      const availableRowIndex = rowRightEdges.findIndex((rightEdge) => window.leftPx >= rightEdge);

      if (availableRowIndex >= 0) {
        rows[availableRowIndex].push(window);
        rowRightEdges[availableRowIndex] = visualRightEdge;
        continue;
      }

      if (rows.length >= BUFF_AXIS_MAX_ROWS) {
        hiddenCount += 1;
        continue;
      }

      rows.push([window]);
      rowRightEdges.push(visualRightEdge);
    }

    return {
      rows,
      hiddenCount,
      visibleCount: rows.reduce((sum, row) => sum + row.length, 0),
      heightPx: rows.length > 0 ? rows.length * BUFF_AXIS_ROW_HEIGHT_PX + BUFF_AXIS_PADDING_Y_PX * 2 : 0,
    };
  }, [detail.battle.durationMs, detail.timelineEvents, rosterEntries, teamWideBuffKeys, timelineBoardWidth]);

  const laidOutTimelineLanes = useMemo(() => {
    return timelineLanes.map((lane) => {
      const eventFirstHitPositions = lane.displayEvents.map((event) => {
        const firstHitTs = event.hits[0]?.tsMsFromStart ?? event.tsMsFromStart;
        return Math.min(Math.max(firstHitTs / Math.max(detail.battle.durationMs, 1), 0), 1) * timelineBoardWidth;
      });

      const laidOutEvents = lane.displayEvents.map((event, index) => {
        const firstHitPx = eventFirstHitPositions[index] ?? 0;
        const nextFirstHitPx =
          index < eventFirstHitPositions.length - 1
            ? (eventFirstHitPositions[index + 1] ?? timelineBoardWidth)
            : null;
        const rightBoundaryPx =
          nextFirstHitPx === null ? timelineBoardWidth : Math.max(nextFirstHitPx, firstHitPx);
        const availableWidthPx = Math.max(rightBoundaryPx - firstHitPx, TIMELINE_NODE_MIN_VISIBLE_WIDTH_PX);

        // 块宽优先覆盖真实命中跨度（首 hit → 尾 hit），呈钢琴卷帘式连续铺排；
        // 至少放得下标签，最多到下一个块的起点，空隙保留 = 真实的输出空窗。
        const lastHitTs = event.hits.length > 0 ? event.hits[event.hits.length - 1].tsMsFromStart : event.tsMsFromStart;
        const hitSpanPx =
          (Math.min(Math.max(lastHitTs / Math.max(detail.battle.durationMs, 1), 0), 1) * timelineBoardWidth) - firstHitPx;
        const fullLabelWidthPx = estimateTimelineNodeWidthPx(event.eventName);
        let nodeWidthPx = Math.min(
          Math.max(hitSpanPx + 6, Math.min(fullLabelWidthPx, availableWidthPx)),
          availableWidthPx,
        );
        if (nodeWidthPx < TIMELINE_NODE_COMPACT_MIN_WIDTH_PX) {
          nodeWidthPx = Math.max(TIMELINE_NODE_MIN_VISIBLE_WIDTH_PX, availableWidthPx);
        }

        let nodeLeftPx = firstHitPx;
        if (nodeLeftPx + nodeWidthPx > rightBoundaryPx) {
          nodeLeftPx = Math.max(firstHitPx, rightBoundaryPx - nodeWidthPx);
        }
        nodeLeftPx = Math.max(0, Math.min(nodeLeftPx, Math.max(timelineBoardWidth - nodeWidthPx, 0)));

        const nodeLabel =
          nodeWidthPx < TIMELINE_NODE_LABEL_MIN_WIDTH_PX
            ? ""
            : nodeWidthPx < fullLabelWidthPx
              ? getTimelineSkillCompactLabel(event.skillCategory, event.eventName)
              : event.eventName;

        return {
          ...event,
          nodeKey: `${lane.filterKey}-${event.tsMsFromStart}-${event.eventGroupKey ?? event.eventKey ?? event.eventName}-${index}`,
          nodeLabel,
          nodeLeftPx,
          nodeWidthPx,
          hitMarkers: event.hits.map((hit) => ({
            ...hit,
            leftPx:
              Math.min(Math.max(hit.tsMsFromStart / Math.max(detail.battle.durationMs, 1), 0), 1) *
              timelineBoardWidth,
          })),
        };
      });

      return {
        ...lane,
        laidOutEvents,
      };
    });
  }, [detail.battle.durationMs, timelineBoardWidth, timelineLanes]);

  const laidOutLaneBuffs = useMemo(() => {
    const battleDurationMs = Math.max(detail.battle.durationMs, 1);
    type LaneBuffSegment = {
      leftPx: number;
      widthPx: number;
      layers: number;
      startMs: number;
      endMs: number;
    };
    type LaneBuffBar = {
      key: string;
      name: string;
      sourceText: string;
      eventKey: string | null;
      categoryLabel: string;
      categoryClassName: string;
      effectText: string;
      startMs: number;
      endMs: number;
      leftPx: number;
      widthPx: number;
      applyCount: number;
      maxLayers: number;
      segments: LaneBuffSegment[];
    };
    const result = new Map<
      string,
      { rows: LaneBuffBar[][]; hiddenCount: number; alwaysOnNames: string[]; extraHeightPx: number }
    >();

    for (const lane of timelineLanes) {
      const windows = detail.timelineEvents
        .filter(
          (event) =>
            event.laneType === "buff" &&
            (event.durationMs ?? 0) > 0 &&
            !isCosmeticCarrierBuff(event) &&
            (event.targetCharacterName?.trim() ?? "") === lane.characterName &&
            !teamWideBuffKeys.has(getBuffApplicationIdentity(event)),
        )
        .map((event, index) => {
          const startMs = Math.min(Math.max(event.tsMsFromStart, 0), battleDurationMs);
          const endMs = Math.min(Math.max(event.tsMsFromStart + (event.durationMs ?? 0), startMs), battleDurationMs);
          const category = getBuffSourceCategory(event.eventKey);
          return {
            key: `${lane.filterKey}-lanebuff-${event.eventKey ?? event.eventName}-${event.tsMsFromStart}-${index}`,
            groupKey: event.eventKey ?? event.eventName,
            name: normalizeTimelineName(event.eventName, event.eventKey ?? "未知效果"),
            sourceText: event.sourceCharacterName?.trim() || "未知来源",
            eventKey: event.eventKey ?? null,
            categoryLabel: category.label,
            categoryClassName: category.className,
            effectText: formatLaneBuffEffectText(event),
            startMs,
            endMs,
            count: 1,
          };
        })
        .filter((window) => window.endMs > window.startMs)
        .sort((left, right) =>
          left.groupKey === right.groupKey ? left.startMs - right.startMs : left.groupKey.localeCompare(right.groupKey),
        );

      // 同名 buff 的每次施加都是独立窗口；用扫描线算出"同时生效层数"随时间的阶梯，
      // 而不是把 28 次施加合并成一根满格长条（会误导成一直满层）。
      const groups = new Map<string, typeof windows>();
      for (const window of windows) {
        const group = groups.get(window.groupKey) ?? [];
        group.push(window);
        groups.set(window.groupKey, group);
      }

      const bars: LaneBuffBar[] = [];
      for (const group of groups.values()) {
        const first = group[0];
        const points: Array<{ t: number; delta: number }> = [];
        for (const window of group) {
          points.push({ t: window.startMs, delta: 1 }, { t: window.endMs, delta: -1 });
        }
        points.sort((left, right) => left.t - right.t || right.delta - left.delta);

        const spans: Array<{ startMs: number; endMs: number; layers: number }> = [];
        let layers = 0;
        let prevT = points[0]?.t ?? 0;
        for (const point of points) {
          if (point.t > prevT) {
            if (layers > 0) {
              spans.push({ startMs: prevT, endMs: point.t, layers });
            }
            prevT = point.t;
          }
          layers += point.delta;
        }

        const flushBar = (chunk: typeof spans) => {
          if (chunk.length === 0) {
            return;
          }
          const startMs = chunk[0].startMs;
          const endMs = chunk[chunk.length - 1].endMs;
          const leftPx = (startMs / battleDurationMs) * timelineBoardWidth;
          const widthPx = Math.max(((endMs - startMs) / battleDurationMs) * timelineBoardWidth, 6);
          const maxLayers = chunk.reduce((max, span) => Math.max(max, span.layers), 1);
          const applyCount = group.filter((window) => window.startMs <= endMs && window.endMs >= startMs).length;
          bars.push({
            key: `${first.key}-bar-${startMs}`,
            name: first.name,
            sourceText: first.sourceText,
            eventKey: first.eventKey,
            categoryLabel: first.categoryLabel,
            categoryClassName: first.categoryClassName,
            effectText: first.effectText,
            startMs,
            endMs,
            leftPx,
            widthPx,
            applyCount,
            maxLayers,
            segments: chunk.map((span) => ({
              leftPx: ((span.startMs - startMs) / battleDurationMs) * timelineBoardWidth,
              widthPx: Math.max(((span.endMs - span.startMs) / battleDurationMs) * timelineBoardWidth, 1),
              layers: span.layers,
              startMs: span.startMs,
              endMs: span.endMs,
            })),
          });
        };

        let chunk: typeof spans = [];
        for (const span of spans) {
          const last = chunk[chunk.length - 1];
          if (last && span.startMs > last.endMs + LANE_BUFF_MERGE_GAP_MS) {
            flushBar(chunk);
            chunk = [];
          }
          chunk.push(span);
        }
        flushBar(chunk);
      }
      bars.sort((left, right) => left.startMs - right.startMs || right.endMs - left.endMs);

      const alwaysOnNames = new Set<string>();
      const windowed = bars.filter((bar) => {
        // 单层且覆盖近整场的才算常驻；多层 buff 的层数起伏本身就是信息，保留显示。
        if (bar.maxLayers <= 1 && bar.endMs - bar.startMs >= battleDurationMs * LANE_BUFF_ALWAYS_ON_RATIO) {
          alwaysOnNames.add(bar.name);
          return false;
        }
        return true;
      });

      const rows: LaneBuffBar[][] = [];
      let hiddenCount = 0;
      for (const bar of windowed) {
        const leftPx = bar.leftPx;
        let placed = false;
        for (const row of rows) {
          const last = row[row.length - 1];
          if (!last || last.leftPx + last.widthPx + LANE_BUFF_ROW_GAP_PX <= leftPx) {
            row.push(bar);
            placed = true;
            break;
          }
        }
        if (!placed) {
          if (rows.length < LANE_BUFF_MAX_ROWS) {
            rows.push([bar]);
          } else {
            hiddenCount += 1;
          }
        }
      }

      result.set(lane.filterKey, {
        rows,
        hiddenCount,
        alwaysOnNames: [...alwaysOnNames],
        extraHeightPx: rows.length > 0 ? rows.length * LANE_BUFF_ROW_HEIGHT_PX + LANE_BUFF_ROW_GAP_PX : 0,
      });
    }
    return result;
  }, [detail.battle.durationMs, detail.timelineEvents, teamWideBuffKeys, timelineBoardWidth, timelineLanes]);

  const enemyPoiseTrack = useMemo(() => {
    const battleDurationMs = Math.max(detail.battle.durationMs, 1);
    // 每个 ts 取最后一个失衡值（同 tick 多段命中会重复上报同一余量）
    const byTs = new Map<number, { cur: number; value: number; eventName: string }>();
    for (const event of detail.timelineEvents) {
      const poise = event.poiseDamage;
      if (!poise || poise.current_value === null || poise.current_value === undefined) {
        continue;
      }
      byTs.set(event.tsMsFromStart, {
        cur: poise.current_value,
        value: poise.value ?? 0,
        eventName: event.eventName,
      });
    }
    const points = [...byTs.entries()]
      .map(([tsMs, item]) => ({ tsMs, ...item }))
      .sort((left, right) => left.tsMs - right.tsMs);
    if (points.length < 2) {
      return null;
    }
    const maxCur = points.reduce((max, point) => Math.max(max, point.cur), 0);
    if (maxCur <= 0) {
      return null;
    }

    // cur=0 的连续区段 = 破韧/失衡输出窗口，窗口终点取下一个非零采样点
    const breakWindows: Array<{ startMs: number; endMs: number }> = [];
    let zeroStart: number | null = null;
    for (const point of points) {
      if (point.cur <= 0) {
        if (zeroStart === null) {
          zeroStart = point.tsMs;
        }
      } else if (zeroStart !== null) {
        breakWindows.push({ startMs: zeroStart, endMs: point.tsMs });
        zeroStart = null;
      }
    }
    if (zeroStart !== null) {
      breakWindows.push({ startMs: zeroStart, endMs: battleDurationMs });
    }

    const toX = (tsMs: number) => (Math.min(Math.max(tsMs, 0), battleDurationMs) / battleDurationMs) * timelineBoardWidth;
    const height = ENEMY_TRACK_HEIGHT_PX;
    const padY = 8;
    const toY = (cur: number) => padY + (1 - Math.min(Math.max(cur / maxCur, 0), 1)) * (height - padY * 2);
    // 阶梯线：值保持到下一采样点
    let path = `M ${toX(points[0].tsMs).toFixed(1)} ${toY(points[0].cur).toFixed(1)}`;
    for (let index = 1; index < points.length; index += 1) {
      const x = toX(points[index].tsMs);
      path += ` H ${x.toFixed(1)} V ${toY(points[index].cur).toFixed(1)}`;
    }

    const mechanicMarkers = detail.timelineEvents
      .filter((event) => (event.eventKey ?? "").includes("eny_") && event.laneType === "skill")
      .map((event) => ({ tsMs: event.tsMsFromStart, name: event.eventName, x: toX(event.tsMsFromStart) }))
      .filter((marker, index, list) => index === 0 || marker.tsMs !== list[index - 1].tsMs);

    return {
      points: points.map((point) => ({ ...point, x: toX(point.tsMs), y: toY(point.cur) })),
      maxCur,
      path,
      breakWindows: breakWindows.map((window) => ({
        ...window,
        leftPx: toX(window.startMs),
        widthPx: Math.max(toX(window.endMs) - toX(window.startMs), 2),
      })),
      mechanicMarkers,
    };
  }, [detail.battle.durationMs, detail.timelineEvents, timelineBoardWidth]);

  const damageCurves = useMemo(() => {
    const chartPaddingTop = 18;
    const chartPaddingBottom = 18;
    const chartHeight = DAMAGE_CURVE_HEIGHT;
    const usableHeight = chartHeight - chartPaddingTop - chartPaddingBottom;
    const bucketCount = Math.max(1, Math.ceil(detail.battle.durationMs / CURVE_BUCKET_MS));

    const curves = timelineLanes.map((lane) => {
      const bucketTotals = new Float64Array(bucketCount);

      for (const event of detail.timelineEvents) {
        if (event.laneType !== "skill") {
          continue;
        }
        const bucketIndex = Math.min(
          bucketCount - 1,
          Math.max(0, Math.floor(event.tsMsFromStart / CURVE_BUCKET_MS)),
        );

        if (metric === "rdps") {
          const rdpsValue = (event.rdpsContributions ?? []).reduce((sum, contribution) => {
            const sameCharacterName = contribution.characterName === lane.characterName;
            const sameCharacterKey = lane.characterKey && contribution.characterKey === lane.characterKey;
            return sameCharacterName || sameCharacterKey ? sum + contribution.value : sum;
          }, 0);
          if (rdpsValue > 0) {
            bucketTotals[bucketIndex] += rdpsValue;
          }
          continue;
        }

        if (event.sourceCharacterName === lane.characterName && (event.value ?? 0) > 0) {
          bucketTotals[bucketIndex] += event.value ?? 0;
        }
      }

      let cumulative = 0;
      const points = [{ x: 0, tsMsFromStart: 0, value: 0 }];
      for (let indexInBucket = 0; indexInBucket < bucketCount; indexInBucket += 1) {
        cumulative += bucketTotals[indexInBucket];
        const bucketEndMs = Math.min(detail.battle.durationMs, (indexInBucket + 1) * CURVE_BUCKET_MS);
        const elapsedSeconds = Math.max(bucketEndMs / 1000, 1);
        const metricValue = cumulative / elapsedSeconds;
        const pointX =
          Math.min(Math.max(bucketEndMs / detail.battle.durationMs, 0), 1) * timelineBoardWidth;
        points.push({
          x: pointX,
          tsMsFromStart: bucketEndMs,
          value: metricValue,
        });
      }

      return {
        key: lane.filterKey,
        characterName: lane.characterName,
        color: characterColorByFilterKey.get(lane.filterKey) ?? getDamageElementColor("unknown"),
        currentValue: points[points.length - 1]?.value ?? 0,
        points,
      };
    });

    const maxDamage = Math.max(
      1,
      ...curves.flatMap((curve) => curve.points.map((point) => point.value)),
    );

    return {
      chartHeight,
      chartPaddingTop,
      chartPaddingBottom,
      maxDamage,
      gridValues: [0.25, 0.5, 0.75, 1],
      curves: curves.map((curve) => ({
        ...curve,
        polyline: curve.points
          .map((point) => {
            const y =
              chartHeight -
              chartPaddingBottom -
              (point.value / maxDamage) * usableHeight;
            return `${point.x},${y}`;
          })
          .join(" "),
      })),
    };
  }, [characterColorByFilterKey, detail.battle.durationMs, detail.timelineEvents, metric, timelineBoardWidth, timelineLanes]);

  useEffect(() => {
    setVisibleCurveKeys((current) => {
      const laneKeys = timelineLanes.map((lane) => lane.filterKey);
      if (current.length === 0) {
        return laneKeys;
      }
      const next = current.filter((key) => laneKeys.includes(key));
      for (const key of laneKeys) {
        if (!next.includes(key)) {
          next.push(key);
        }
      }
      return next;
    });
  }, [timelineLanes]);

  const visibleDamageCurves = useMemo(() => {
    return damageCurves.curves.filter((curve) => visibleCurveKeys.includes(curve.key));
  }, [damageCurves.curves, visibleCurveKeys]);

  const curveHoverReadout = useMemo(() => {
    if (!curveHover) {
      return null;
    }

    const entries = visibleDamageCurves.map((curve) => {
      let currentValue = 0;
      for (const point of curve.points) {
        if (point.tsMsFromStart > curveHover.tsMsFromStart) {
          break;
        }
        currentValue = point.value;
      }
      return {
        key: curve.key,
        characterName: curve.characterName,
        color: curve.color,
        value: currentValue,
      };
    });

    return {
      label: formatDurationMs(curveHover.tsMsFromStart),
      entries: entries.sort((left, right) => right.value - left.value),
      left: Math.min(Math.max(curveHover.x + 14, 8), Math.max(timelineBoardWidth - 196, 8)),
      lineLeft: curveHover.x,
    };
  }, [curveHover, timelineBoardWidth, visibleDamageCurves]);

  function toggleCurveVisibility(curveKey: string) {
    setVisibleCurveKeys((current) =>
      current.includes(curveKey) ? current.filter((key) => key !== curveKey) : [...current, curveKey],
    );
  }

  function handleCurvePointerMove(event: React.MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const localX = Math.min(Math.max(event.clientX - rect.left, 0), rect.width);
    const normalizedX = rect.width > 0 ? localX / rect.width : 0;
    setCurveHover({
      x: normalizedX * timelineBoardWidth,
      tsMsFromStart: Math.round(normalizedX * detail.battle.durationMs),
    });
  }

  function handleBuffPointerMove(event: React.MouseEvent<HTMLDivElement>, buff: BuffAxisWindow) {
    const rect = event.currentTarget.getBoundingClientRect();
    const localX = Math.min(Math.max(event.clientX - rect.left, 0), rect.width);
    const ratio = rect.width > 0 ? localX / rect.width : 0;
    setBuffHover({
      buff,
      tsMsFromStart: Math.round(buff.startMs + ratio * Math.max(buff.durationMs, 1)),
      x: event.clientX,
      y: event.clientY,
    });
  }

  const uploaderName =
    detail.battle.roster[0]?.accountDisplayName ??
    detail.participants[0]?.accountDisplayName ??
    "未知上传者";

  const battleStartedAt = new Date(detail.battle.battleStartAt).toLocaleString("zh-CN", {
    hour12: false,
  });
  const timelineZeroLabel = getTimelineZeroSourceLabel(detail.battle.timelineZeroSource ?? detail.battle.timeSource);
  const durationSourceNote = detail.battle.timerWindowValid === false
    ? "计时窗口异常，本场不计入榜单"
    : detail.battle.timerStartInferred
    ? `时间轴零点：${timelineZeroLabel}`
    : detail.battle.officialTimerStartSeen && detail.battle.officialTimerEndSeen
      ? "官方计时窗口"
      : detail.battle.timerStartSeen || detail.battle.timerEndSeen
        ? `计时窗口：${timelineZeroLabel}`
        : "整场记录的有效战斗时间";
  const hasContractTags = hasContractTagData(detail.battle.contractTagScore, detail.battle.contractTags);

  const buffHoverSegments = buffHover
    ? getBuffTooltipSegments(buffHover.buff, buffHover.tsMsFromStart)
    : [];
  const buffTooltipSegments = buffHover
    ? dedupeBuffTooltipSegments(
        buffHoverSegments.length > 0 ? buffHoverSegments : buffHover.buff.segments,
        buffHover.tsMsFromStart,
      )
    : [];

  function getTimelineLeft(tsMsFromStart: number) {
    const progress = Math.min(Math.max(tsMsFromStart / detail.battle.durationMs, 0), 1);
    return `${progress * 100}%`;
  }

  function getTimelineWidth(tsMsFromStart: number, durationMs: number | null) {
    const minimumPercent = (24 / Math.max(timelineBoardWidth, 1)) * 100;
    if (!durationMs) {
      return `${minimumPercent}%`;
    }

    const available = Math.max(detail.battle.durationMs - tsMsFromStart, 1);
    const effectiveDuration = Math.min(durationMs, available);
    const width = Math.max((effectiveDuration / detail.battle.durationMs) * 100, minimumPercent);
    return `${width}%`;
  }

  async function handleDeleteBattle() {
    const confirmed = window.confirm("确认删除这条公开战斗记录吗？删除后将无法继续浏览。");
    if (!confirmed) {
      return;
    }

    setDeleteState("submitting");
    setDeleteMessage(null);

    try {
      const response = await fetch(buildApiUrl(`/api/battles/${detail.battle.id}`), {
        method: "DELETE",
        credentials: "include",
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
        throw new Error(payload?.error?.message ?? "删除失败。");
      }

      setDeleteMessage("记录已删除，正在返回首页。");
      router.push("/");
      router.refresh();
    } catch (error) {
      setDeleteMessage(error instanceof Error ? error.message : "删除失败。");
    } finally {
      setDeleteState("idle");
    }
  }

  return (
    <div className={`page-stack${axisOnly ? " axis-compact" : ""}`}>
      <section className="panel panel-muted" style={{ display: "grid", gap: 16 }}>
        <div className="breadcrumbs">
          首页 / 竞速榜单 / {battleTitle} / 战斗详情
        </div>
        <div className="section-heading">
          <div>
            <div className="eyebrow">{battleEyebrow}</div>
            <h1>{battleTitle}</h1>
            <p className="muted" style={{ margin: "8px 0 0" }}>
              这是一份整场战斗报告，时间轴、技能分布和统计表会跟随左侧角色筛选同步变化。
            </p>
          </div>
          <div style={{ display: "grid", gap: 8, justifyItems: "end" }}>
            {ROTATIONS_ENABLED ? <Link className="button-primary" href={`/axis/${detail.battle.id}/editor`}>
              排轴编辑器
            </Link> : null}
            {ROTATIONS_ENABLED && !axisOnly ? (
              <Link className="button-secondary" href={`/axis/${detail.battle.id}`}>
                排轴视图
              </Link>
            ) : axisOnly ? (
              <Link className="button-secondary" href={`/battle/${detail.battle.id}`}>
                战斗详情
              </Link>
            ) : null}
            {detail.viewerCapabilities.canDelete ? (
              <button
                className="button-danger"
                disabled={deleteState === "submitting"}
                onClick={handleDeleteBattle}
                style={{ cursor: deleteState === "submitting" ? "wait" : "pointer" }}
                type="button"
              >
                {deleteState === "submitting" ? "删除中..." : "删除这条记录"}
              </button>
            ) : null}
            <span className="pill">{detail.battle.clearFlag ? "已通关" : "未通关"}</span>
          </div>
        </div>
        <div className="stat-grid stat-grid-4">
          <div className="metric-card">
            <span className="metric-label">通关时长</span>
            <strong>{formatDurationMs(detail.battle.durationMs)}</strong>
            <span className="metric-note">{durationSourceNote}</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">{battleMetricLabel}</span>
            <strong>{Math.round(battleMetricValue).toLocaleString()}</strong>
            <span className="metric-note">{battleMetricNote}</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">总伤害</span>
            <strong>{detail.battle.totalDamage.toLocaleString()}</strong>
            <span className="metric-note">按整场战斗统计</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">上传者</span>
            <strong>{uploaderName}</strong>
            <span className="metric-note">{battleStartedAt}</span>
          </div>
        </div>
        {hasContractTags ? (
          <div style={{ display: "grid", gap: 8 }}>
            <div className="eyebrow">所选 tag</div>
            <ContractTagSummary score={detail.battle.contractTagScore} tags={detail.battle.contractTags} />
          </div>
        ) : null}
      </section>

      {deleteMessage ? (
        <section className="panel">
          <span className="muted">{deleteMessage}</span>
        </section>
      ) : null}

      <div className="report-layout">
        <aside className="detail-stack">
          <section className="panel timeline-roster-panel" style={{ display: "grid", gap: 14 }}>
            <div className="timeline-roster-header">
              <div className="eyebrow">阵容筛选</div>
              <h2 style={{ margin: "6px 0 0" }}>本场角色</h2>
            </div>
            <div className="directory-list timeline-roster-directory">
              <button
                className={`directory-button roster-link${selectedCharacterKey === null ? " is-active" : ""}`}
                onClick={() => setSelectedCharacterKey(null)}
                type="button"
              >
                <strong>全部角色</strong>
                <small>查看整场时间轴与完整统计</small>
              </button>
              {rosterEntries.map((entry) => (
                <button
                  className={`directory-button roster-link${selectedCharacterKey === entry.filterKey ? " is-active" : ""}`}
                  key={entry.slot}
                  onClick={() =>
                    setSelectedCharacterKey((current) =>
                      current === entry.filterKey ? null : entry.filterKey,
                    )
                  }
                  type="button"
                >
                  <strong className="character-inline">
                    <CharacterAvatar
                      avatarUrl={entry.characterAvatarUrl}
                      name={entry.characterName}
                      size="sm"
                    />
                    <span>{entry.characterName}</span>
                  </strong>
                  <span className="roster-meta">
                    <span>{entry.participant ? `DPS ${Math.round(entry.participant.dps).toLocaleString()}` : "DPS -"}</span>
                    <span>{entry.participant ? `rDPS ${Math.round(entry.participant.rdps).toLocaleString()}` : "rDPS -"}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>

          {!axisOnly && detail.viewerCapabilities.canViewVersions ? (
            <section className="panel">
              <div className="eyebrow">上传者可见</div>
              <h2 style={{ margin: "6px 0 12px" }}>解析信息</h2>
              <div className="info-list">
                <span>解析器版本：{detail.battle.parserVersion}</span>
                <span>规则版本：{detail.battle.rulesVersion}</span>
                <span>时间轴零点：{timelineZeroLabel}</span>
                <span>
                  计时器：开始{detail.battle.timerStartSeen ? "已记录" : "未记录"}，结束
                  {detail.battle.timerEndSeen ? "已记录" : "未记录"}
                </span>
                <span>
                  官方计时：开始{detail.battle.officialTimerStartSeen ? "已记录" : "未记录"}，结束
                  {detail.battle.officialTimerEndSeen ? "已记录" : "未记录"}
                </span>
                <span>
                  计时窗口：
                  {detail.battle.timerWindowValid === true
                    ? "有效"
                    : detail.battle.timerWindowValid === false
                      ? "异常（已阻止通关与榜单准入）"
                      : "旧记录未提供审计结果"}
                </span>
                <span>
                  计时完整性：
                  {detail.battle.timerIntegrityOk === true
                    ? `通过（官方 ${detail.battle.durationMs}ms；观测 ${detail.battle.officialTimerObservedElapsedMs ?? "-"}ms；战斗包 ${detail.battle.packetTimerElapsedMs ?? "-"}ms）`
                    : detail.battle.timerIntegrityOk === false
                      ? `未通过（${detail.battle.timerIntegrityReasons.join("、") || "时序不一致"}）`
                      : "旧记录未提供审计结果"}
                </span>
                <span>
                  rDPS 算法准入：
                  {detail.battle.rdpsRankingEligible === true
                    ? "符合现行准入规则"
                    : "未达到现行准入要求"}
                </span>
                <span>
                  详细证据：
                  {detail.battle.rdpsStrictOk === true
                    ? "完整"
                    : detail.battle.rdpsStrictOk === false
                      ? `仍有 ${detail.battle.rdpsPreflightBlockerCount ?? 0} 项待确认`
                      : "旧记录未提供审计结果"}
                </span>
                <span>
                  身份来源：Boss {detail.battle.bossIdentitySource ?? "旧记录未知"}；副本
                  {detail.battle.dungeonIdentitySource ?? "旧记录未知"}；官方场地
                  {detail.battle.dungeonContextId ?? "旧记录未知"}
                </span>
                <span>
                  配装来源：
                  {detail.battle.loadoutFallbackUsed === true
                    ? "使用完整性证明补字段（角色统计已排除）"
                    : detail.battle.loadoutFallbackUsed === false
                      ? "战斗日志"
                      : "旧记录未知"}
                </span>
                <span style={{ wordBreak: "break-all" }}>指纹：{detail.battle.battleFingerprint}</span>
              </div>
            </section>
          ) : null}

          <section className="panel panel-muted">
            <div className="eyebrow">阅读说明</div>
              <div className="info-list" style={{ marginTop: 10 }}>
              <span>时间轴按统一战斗时间排序，顶部显示全局增益/易伤窗口，角色轨道显示技能命中事件。</span>
              <span>点击左侧角色后，时间轴、技能分布和统计表会同步过滤。</span>
            </div>
          </section>
        </aside>

        <div className="detail-stack">
          {!axisOnly && <CriticalLuckPanel events={detail.timelineEvents} durationMs={detail.battle.durationMs} characterName={selectedCharacterName} />}
          {!axisOnly ? (
          <section className="panel contribution-section">
            <div className="table-toolbar contribution-section-head">
              <div>
                <div className="eyebrow">{selectedCharacterName ? "角色占比" : "全场占比"}</div>
                <h2 style={{ margin: "6px 0 0" }}>
                  {selectedCharacterName ? `${selectedCharacterName} 输出拆分` : "战斗贡献拆分"}
                </h2>
              </div>
              <span className="muted">
                {selectedCharacterName
                  ? "按当前选中角色自己的技能块和伤害事件统计。"
                  : "按本场全部干员和整场伤害事件统计，不受左侧角色筛选影响。"}
              </span>
            </div>
            <div className={`contribution-grid${selectedCharacterName ? " contribution-grid-two" : ""}`}>
              {selectedCharacterName ? (
                <>
                  <ContributionDonutChart
                    entries={selectedCharacterSkillTypeContributionEntries}
                    subtitle="按该角色普攻、战技、连携技、终结技和其他伤害汇总"
                    title="技能类型占比"
                  />
                  <ContributionDonutChart
                    entries={selectedCharacterDamageTypeContributionEntries}
                    subtitle="按该角色技能伤害属性汇总"
                    title="伤害属性占比"
                  />
                </>
              ) : (
                <>
                  <ContributionDonutChart
                    entries={dpsContributionEntries}
                    subtitle="按本场角色 DPS 汇总"
                    title="DPS 占比"
                  />
                  <ContributionDonutChart
                    entries={rdpsContributionEntries}
                    subtitle="按本场角色 rDPS 汇总"
                    title="rDPS 占比"
                  />
                  <ContributionDonutChart
                    entries={damageTypeContributionEntries}
                    subtitle="按整场技能伤害类型汇总"
                    title="伤害类型占比"
                  />
                </>
              )}
            </div>
          </section>
          ) : null}

          <section className="panel timeline-tech-panel" style={{ display: "grid", gap: 14 }}>
            <div className="section-heading timeline-tech-heading">
              <div>
                <div className="eyebrow">战斗时间轴</div>
                <h2>轨道时间轴</h2>
              </div>
            </div>

            {laidOutTimelineLanes.length > 0 ? (
                <div className="timeline-shell timeline-shell-tech">
                <div className="timeline-sidebar">
                  <div className="timeline-sidebar-spacer" />
                  {laidOutBuffAxis.rows.length > 0 ? (
                    <div className="timeline-buff-label" style={{ height: `${laidOutBuffAxis.heightPx}px` }}>
                      <strong>Buff 轴</strong>
                      {laidOutBuffAxis.hiddenCount > 0 ? (
                        <small>隐藏 {laidOutBuffAxis.hiddenCount} 条重叠效果</small>
                      ) : null}
                    </div>
                  ) : null}
                  {laidOutTimelineLanes.map((lane) => {
                    const displayEquipSlots = getDisplayEquipSlots(lane.equips);
                    const laneBuffExtraPx = laidOutLaneBuffs.get(lane.filterKey)?.extraHeightPx ?? 0;
                    return (
                    <div
                      className="timeline-lane-label"
                      key={`label-${lane.slot}`}
                      style={laneBuffExtraPx > 0 ? { height: `calc(var(--timeline-lane-height) + ${laneBuffExtraPx}px)` } : undefined}
                    >
                      <div className="timeline-character-card">
                        <div className="timeline-character-head">
                            <CharacterAvatar
                              avatarUrl={lane.characterAvatarUrl}
                              name={lane.characterName}
                              size="md"
                            />
                          <div className="timeline-character-copy">
                            <strong>{lane.characterName}</strong>
                            <div className="timeline-character-badges">
                              <span className="timeline-mini-pill is-accent">
                                潜{lane.characterPotential ?? "-"}
                              </span>
                                {lane.weapon?.weaponRefine !== null && lane.weapon?.weaponRefine !== undefined ? (
                                  <span className="timeline-mini-pill">精{lane.weapon.weaponRefine}</span>
                                ) : null}
                              </div>
                            </div>
                          </div>

                        <div className="timeline-loadout-row">
                          <LoadoutIcon
                            fallback="武"
                            iconUrl={lane.weapon?.iconUrl}
                            title={
                              lane.weapon
                                ? `${lane.weapon.weaponName}${lane.weapon.weaponRefine !== null && lane.weapon.weaponRefine !== undefined ? ` · 精炼 ${lane.weapon.weaponRefine}` : ""}`
                                : "武器未记录"
                            }
                          />
                          {displayEquipSlots.map((equip, slotIndex) => {
                            return (
                              <LoadoutIcon
                                fallback={`装${slotIndex + 1}`}
                                iconUrl={equip?.iconUrl}
                                isEmpty={!equip}
                                key={`${lane.slot}-equip-${slotIndex}`}
                                title={
                                  equip
                                    ? `${equip.pieceName}${equip.suitName ? ` · ${equip.suitName}` : ""}${equip.partName ? ` · ${equip.partName}` : ""}`
                                    : `装备槽 ${slotIndex + 1}`
                                }
                              />
                            );
                          })}
                        </div>
                      </div>
                    </div>
                    );
                  })}
                  {enemyPoiseTrack ? (
                    <div className="timeline-enemy-label" style={{ height: `${ENEMY_TRACK_HEIGHT_PX}px` }}>
                      <strong>敌方 · 失衡</strong>
                      <small>上限 {Math.round(enemyPoiseTrack.maxCur)} · 破韧 {enemyPoiseTrack.breakWindows.length} 次</small>
                    </div>
                  ) : null}
                </div>

                <div className="timeline-board-wrap timeline-board-wrap-tech" ref={timelineWrapRef}>
                  <div className="timeline-board timeline-board-tech" style={{ width: `${timelineBoardWidth}px` }}>
                    <div className="timeline-scale-track">
                      {timelineMarks.map((mark) => (
                        <div className="timeline-scale-mark" key={mark.left} style={{ left: mark.left }}>
                          <span>{mark.label}</span>
                        </div>
                      ))}
                    </div>

                    {laidOutBuffAxis.rows.length > 0 ? (
                      <div className="timeline-buff-track" style={{ height: `${laidOutBuffAxis.heightPx}px` }}>
                        {timelineMarks.map((mark) => (
                          <div
                            className="timeline-buff-grid"
                            key={`buff-grid-${mark.left}`}
                            style={{ left: mark.left }}
                          />
                        ))}
                        {laidOutBuffAxis.rows.map((row, rowIndex) =>
                          row.map((buff) => (
                            <div
                              className={`timeline-buff-bar ${buff.kind === "enemy" ? "is-enemy-debuff" : "is-ally-buff"}`}
                              key={buff.id}
                              style={{
                                left: `${buff.leftPx}px`,
                                top: `${BUFF_AXIS_PADDING_Y_PX + rowIndex * BUFF_AXIS_ROW_HEIGHT_PX}px`,
                                width: `${buff.widthPx}px`,
                              }}
                              title={`${buff.name} | ${buff.sourceText} | 实际 ${formatTimelineOffsetMs(
                                buff.actualStartMs,
                              )} - ${formatTimelineOffsetMs(buff.actualEndMs)}`}
                              onMouseLeave={() => setBuffHover(null)}
                              onMouseMove={(event) => handleBuffPointerMove(event, buff)}
                            >
                              <span className="timeline-buff-name">{buff.name}</span>
                              <span className="timeline-buff-source">{buff.sourceText}</span>
                            </div>
                          )),
                        )}
                      </div>
                    ) : null}

                    {laidOutTimelineLanes.map((lane) => {
                      const laneBuffs = laidOutLaneBuffs.get(lane.filterKey);
                      const laneBuffExtraPx = laneBuffs?.extraHeightPx ?? 0;
                      return (
                      <div
                        className="timeline-lane-track"
                        key={`track-${lane.slot}`}
                        style={laneBuffExtraPx > 0 ? { height: `calc(var(--timeline-lane-height) + ${laneBuffExtraPx}px)` } : undefined}
                      >
                        <div className="timeline-lane-line" />
                        {timelineMarks.map((mark) => (
                          <div
                            className="timeline-lane-grid"
                            key={`${lane.slot}-${mark.left}`}
                            style={{ left: mark.left }}
                          />
                        ))}
                        {lane.laidOutEvents.map((event, index) => (
                          <div
                            className={`timeline-node timeline-node-${event.laneType} timeline-node-category-${event.skillCategory}${
                              event.important ? " is-important" : ""
                            }${event.nodeLabel ? "" : " is-mini"}`}
                            key={`${lane.slot}-${event.tsMsFromStart}-${index}`}
                            style={{
                              left: `${event.nodeLeftPx}px`,
                              width: `${event.nodeWidthPx}px`,
                            }}
                            onMouseEnter={() => setHoveredTimelineNodeKey(event.nodeKey)}
                            onMouseLeave={() => {
                              setHoveredTimelineNodeKey(null);
                              setSkillHover(null);
                            }}
                            onMouseMove={(mouseEvent) =>
                              setSkillHover({
                                x: mouseEvent.clientX,
                                y: mouseEvent.clientY,
                                name: event.eventName,
                                categoryLabel: event.skillCategoryLabel,
                                tsMsFromStart: event.tsMsFromStart,
                                totalValue: event.value ?? null,
                                hitCount: event.hits.length,
                                maxHit: event.hits.reduce(
                                  (max: number, hit: { value: number | null }) => Math.max(max, hit.value ?? 0),
                                  0,
                                ),
                                damageElement: getTimelineEventDamageElement(event),
                                sourceName: event.sourceCharacterName ?? "未知",
                                targetName: event.targetCharacterName ?? null,
                                eventKey: event.eventKey ?? null,
                              })
                            }
                          >
                            {event.nodeLabel ? <div className="timeline-node-title">{event.nodeLabel}</div> : null}
                          </div>
                        ))}
                        {lane.laidOutEvents.flatMap((event, eventIndex) =>
                          event.hitMarkers.map((hit, hitIndex) => (
                            <div
                              className={`timeline-hit-marker${
                                hoveredTimelineNodeKey === event.nodeKey ? " is-highlighted" : ""
                              }${hoveredTimelineNodeKey && hoveredTimelineNodeKey !== event.nodeKey ? " is-dimmed" : ""}`}
                              key={`${lane.slot}-hit-${eventIndex}-${hitIndex}-${hit.tsMsFromStart}`}
                              style={{ left: `${hit.leftPx}px` }}
                              title={`${event.eventName} | 单 hit ${(Math.round(hit.value ?? 0)).toLocaleString("zh-CN")} | ${formatDurationMs(hit.tsMsFromStart)}`}
                            />
                          )),
                        )}
                        {(laneBuffs?.rows ?? []).map((row, rowIndex) =>
                          row.map((bar) => (
                            <div
                              className={`timeline-lane-buff-bar ${bar.categoryClassName}`}
                              key={bar.key}
                              style={{
                                left: `${bar.leftPx}px`,
                                width: `${bar.widthPx}px`,
                                bottom: `${LANE_BUFF_ROW_GAP_PX + ((laneBuffs?.rows.length ?? 1) - 1 - rowIndex) * LANE_BUFF_ROW_HEIGHT_PX}px`,
                              }}
                              title={`${bar.categoryLabel ? `[${bar.categoryLabel}] ` : ""}${bar.name}${bar.maxLayers > 1 ? ` 峰值${bar.maxLayers}层` : ""}${bar.applyCount > 1 ? ` · 施加${bar.applyCount}次` : ""}${bar.effectText ? ` | ${bar.maxLayers > 1 ? "单层 " : ""}${bar.effectText}` : ""} | 来源 ${bar.sourceText} | ${formatTimelineOffsetMs(bar.startMs)} - ${formatTimelineOffsetMs(bar.endMs)}${bar.eventKey ? ` | ${bar.eventKey}` : ""}`}
                            >
                              {bar.segments.map((segment, segmentIndex) => (
                                <div
                                  className="timeline-lane-buff-seg"
                                  key={`${bar.key}-seg-${segmentIndex}`}
                                  style={{
                                    left: `${segment.leftPx}px`,
                                    width: `${segment.widthPx}px`,
                                    opacity: 0.3 + 0.7 * (segment.layers / bar.maxLayers),
                                  }}
                                  title={`${bar.name} ${segment.layers}层 | ${formatTimelineOffsetMs(segment.startMs)} - ${formatTimelineOffsetMs(segment.endMs)}`}
                                >
                                  {bar.maxLayers > 1 && segment.widthPx >= 13 ? (
                                    <span className="timeline-lane-buff-seg-num">{segment.layers}</span>
                                  ) : null}
                                </div>
                              ))}
                              <span className="timeline-lane-buff-name">
                                {bar.categoryLabel ? `${bar.categoryLabel}·` : ""}
                                {bar.name}
                                {bar.effectText
                                  ? ` ${bar.effectText.startsWith(bar.name) ? bar.effectText.slice(bar.name.length).trimStart() : bar.effectText}`
                                  : ""}
                              </span>
                            </div>
                          )),
                        )}
                        {laneBuffs && (laneBuffs.hiddenCount > 0 || laneBuffs.alwaysOnNames.length > 0) ? (
                          <span
                            className="timeline-lane-buff-overflow"
                            title={[
                              laneBuffs.alwaysOnNames.length > 0
                                ? `常驻：${laneBuffs.alwaysOnNames.join("、")}`
                                : "",
                              laneBuffs.hiddenCount > 0 ? `受行数限制隐藏 ${laneBuffs.hiddenCount} 段重叠窗口` : "",
                            ]
                              .filter(Boolean)
                              .join(" | ")}
                          >
                            {laneBuffs.alwaysOnNames.length > 0 ? `常驻${laneBuffs.alwaysOnNames.length}` : ""}
                            {laneBuffs.alwaysOnNames.length > 0 && laneBuffs.hiddenCount > 0 ? " · " : ""}
                            {laneBuffs.hiddenCount > 0 ? `+${laneBuffs.hiddenCount}` : ""}
                          </span>
                        ) : null}
                      </div>
                      );
                    })}

                    {enemyPoiseTrack ? (
                      <div className="timeline-enemy-track" style={{ height: `${ENEMY_TRACK_HEIGHT_PX}px` }}>
                        {timelineMarks.map((mark) => (
                          <div className="timeline-lane-grid" key={`enemy-grid-${mark.left}`} style={{ left: mark.left }} />
                        ))}
                        {enemyPoiseTrack.breakWindows.map((window, windowIndex) => (
                          <div
                            className="timeline-enemy-break"
                            key={`break-${windowIndex}`}
                            style={{ left: `${window.leftPx}px`, width: `${window.widthPx}px` }}
                            title={`破韧窗口 ${formatTimelineOffsetMs(window.startMs)} - ${formatTimelineOffsetMs(window.endMs)}（${((window.endMs - window.startMs) / 1000).toFixed(1)}s）`}
                          >
                            <span>破韧</span>
                          </div>
                        ))}
                        <svg
                          className="timeline-enemy-svg"
                          height={ENEMY_TRACK_HEIGHT_PX}
                          preserveAspectRatio="none"
                          width={timelineBoardWidth}
                        >
                          <path className="timeline-enemy-poise-path" d={enemyPoiseTrack.path} />
                        </svg>
                        {enemyPoiseTrack.points.map((point, pointIndex) => (
                          <div
                            className="timeline-enemy-point"
                            key={`poise-pt-${pointIndex}`}
                            style={{ left: `${point.x}px`, top: `${point.y}px` }}
                            title={`失衡 ${Math.round(point.cur)}/${Math.round(enemyPoiseTrack.maxCur)}（${point.value > 0 ? "+" : ""}${point.value}） | ${point.eventName} | ${formatTimelineOffsetMs(point.tsMs)}`}
                          />
                        ))}
                        {enemyPoiseTrack.mechanicMarkers.map((marker, markerIndex) => (
                          <div
                            className="timeline-enemy-mechanic"
                            key={`mech-${markerIndex}`}
                            style={{ left: `${marker.x}px` }}
                            title={`敌方机制 ${marker.name} | ${formatTimelineOffsetMs(marker.tsMs)}`}
                          />
                        ))}
                      </div>
                    ) : null}

                    <div className="timeline-curve-panel">
                      <div className="timeline-curve-head">
                        <div className="timeline-curve-title">
                          <strong>{curveMetricLabel} 曲线（每秒）</strong>
                          <span className="muted">
                            按 1 秒桶累计后折算成到当前时刻为止的平均 {curveMetricLabel}，和当前榜单口径保持同步。
                          </span>
                        </div>
                        <div className="timeline-curve-toggle-list">
                          {damageCurves.curves.map((curve) => {
                            const isActive = visibleCurveKeys.includes(curve.key);
                            return (
                              <button
                                className={`timeline-curve-toggle${isActive ? " is-active" : ""}`}
                                key={`toggle-${curve.key}`}
                                onClick={() => toggleCurveVisibility(curve.key)}
                                type="button"
                              >
                                <span
                                  className="timeline-curve-toggle-dot"
                                  style={{ backgroundColor: curve.color }}
                                />
                                <span>{curve.characterName}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                      <div
                        className="timeline-curve-chart"
                        onMouseLeave={() => setCurveHover(null)}
                        onMouseMove={handleCurvePointerMove}
                        style={{ height: `${damageCurves.chartHeight}px` }}
                      >
                        {timelineMarks.map((mark) => (
                          <div
                            className="timeline-curve-vertical"
                            key={`curve-v-${mark.left}`}
                            style={{ left: mark.left }}
                          />
                        ))}
                        {damageCurves.gridValues.map((value) => (
                          <div
                            className="timeline-curve-horizontal"
                            key={`curve-h-${value}`}
                            style={{
                              top: `${damageCurves.chartPaddingTop + (1 - value) * (damageCurves.chartHeight - damageCurves.chartPaddingTop - damageCurves.chartPaddingBottom)}px`,
                            }}
                          />
                        ))}
                        {curveHoverReadout ? (
                          <>
                            <div
                              className="timeline-curve-cursor"
                              style={{ left: `${curveHoverReadout.lineLeft}px` }}
                            />
                            <div
                              className="timeline-curve-tooltip"
                              style={{ left: `${curveHoverReadout.left}px` }}
                            >
                              <strong>{curveHoverReadout.label}</strong>
                              {curveHoverReadout.entries.map((entry) => (
                                <div className="timeline-curve-tooltip-row" key={`tooltip-${entry.key}`}>
                                  <span className="timeline-curve-tooltip-name">
                                  <span
                                      className="timeline-curve-toggle-dot"
                                      style={{ backgroundColor: entry.color }}
                                    />
                                    <span>{entry.characterName}</span>
                                  </span>
                                  <span>{formatCurveMetricValue(entry.value)}</span>
                                </div>
                              ))}
                            </div>
                          </>
                        ) : null}
                        <svg
                          aria-label={`${curveMetricLabel} 曲线`}
                          className="timeline-curve-svg"
                          height={damageCurves.chartHeight}
                          viewBox={`0 0 ${timelineBoardWidth} ${damageCurves.chartHeight}`}
                          width={timelineBoardWidth}
                        >
                          {visibleDamageCurves.map((curve) => (
                            <polyline
                              className="timeline-curve-line"
                              key={curve.key}
                              points={curve.polyline}
                              stroke={curve.color}
                            />
                          ))}
                        </svg>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="empty-state">当前筛选条件下没有可显示的时间轴事件。</div>
            )}

          </section>

          {!axisOnly ? (
          <div className="report-bottom">
            <section className="panel">
              <div className="table-toolbar">
                <div>
                  <div className="eyebrow">技能分布</div>
                  <h2 style={{ margin: "6px 0 0" }}>角色技能构成</h2>
                </div>
                <span className="muted">同一角色的技能分支按正式技能归并；命中数、平均和最大按单次伤害命中统计，缺少完整命中记录时显示 —。</span>
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>角色</th>
                      <th>技能</th>
                      <th>命中数</th>
                      <th>总伤</th>
                      <th>占比</th>
                      <th>平均命中</th>
                      <th>最大命中</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSkills.map((stat, statIndex) => (
                      <tr key={`${stat.characterName}-${stat.skillKey ?? stat.skillName}-${statIndex}`}>
                        <td>
                          <span className="character-inline">
                            <CharacterAvatar
                              avatarUrl={
                                detail.battle.roster.find((entry) => entry.characterName === stat.characterName)
                                  ?.characterAvatarUrl
                              }
                              name={stat.characterName}
                              size="sm"
                            />
                            <span>{stat.characterName}</span>
                          </span>
                        </td>
                        <td>{getPreferredBattleSkillDisplayName(stat.skillName, stat.skillKey)}</td>
                        <td>{stat.hitCount ?? "—"}</td>
                        <td>{stat.totalDamage.toLocaleString()}</td>
                        <td>{formatSkillDamageShare(stat.damageShare)}</td>
                        <td>{stat.avgDamage === null ? "—" : Math.round(stat.avgDamage).toLocaleString()}</td>
                        <td>{stat.maxHit === null ? "—" : stat.maxHit.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel">
              <div className="table-toolbar">
                <div>
                  <div className="eyebrow">参战角色</div>
                  <h2 style={{ margin: "6px 0 0" }}>战斗统计表</h2>
                </div>
                <span className="muted">包含 DPS、rDPS、总伤、最大单次与暴击率。</span>
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>角色</th>
                      <th>DPS</th>
                      <th>rDPS</th>
                      <th>总伤</th>
                      <th>最大单次</th>
                      <th>暴击率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredParticipants.map((participant) => (
                      <tr key={participant.characterName}>
                        <td>
                          <span className="character-inline">
                            <CharacterAvatar
                              avatarUrl={participant.characterAvatarUrl}
                              name={participant.characterName}
                              size="sm"
                            />
                            <span>{participant.characterName}</span>
                          </span>
                        </td>
                        <td>{Math.round(participant.dps).toLocaleString()}</td>
                        <td>{Math.round(participant.rdps).toLocaleString()}</td>
                        <td>{participant.totalDamage.toLocaleString()}</td>
                        <td>{participant.maxHit?.toLocaleString() ?? "-"}</td>
                        <td>{participant.critRate != null ? `${Math.round(participant.critRate * 100)}%` : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
          ) : null}
        </div>
      </div>
      {skillHover ? (
        <div
          className="timeline-buff-tooltip timeline-skill-tooltip"
          style={{
            left: `${skillHover.x + 14}px`,
            top: `${skillHover.y + 14}px`,
          }}
        >
          <strong>{skillHover.name}</strong>
          <span>
            {skillHover.name === skillHover.categoryLabel ? "" : `${skillHover.categoryLabel} · `}
            {formatTimelineOffsetMs(skillHover.tsMsFromStart)}
          </span>
          <span>
            {skillHover.totalValue === null && skillHover.maxHit === 0
              ? "零伤害施放"
              : `总伤 ${Math.round(skillHover.totalValue ?? 0).toLocaleString("zh-CN")} · ${skillHover.hitCount} hit${
                  skillHover.hitCount > 1 ? ` · 最大单跳 ${Math.round(skillHover.maxHit).toLocaleString("zh-CN")}` : ""
                }`}
          </span>
          {skillHover.damageElement ? (
            <span>伤害属性：{DAMAGE_ELEMENT_LABELS[skillHover.damageElement] ?? skillHover.damageElement}</span>
          ) : null}
          <span>
            {skillHover.sourceName}
            {skillHover.targetName ? ` → ${skillHover.targetName}` : ""}
          </span>
          {skillHover.eventKey ? <span className="timeline-skill-tooltip-key">{skillHover.eventKey}</span> : null}
        </div>
      ) : null}
      {buffHover ? (
        <div
          className="timeline-buff-tooltip"
          style={{
            left: `${buffHover.x + 14}px`,
            top: `${buffHover.y + 14}px`,
          }}
        >
          <strong>{buffHover.buff.name}</strong>
          <span>
            指针 {formatTimelineOffsetMs(buffHover.tsMsFromStart)} · 可见{" "}
            {formatTimelineOffsetMs(buffHover.buff.startMs)} - {formatTimelineOffsetMs(buffHover.buff.endMs)}
          </span>
          <span>
            实际：{formatTimelineOffsetMs(buffHover.buff.actualStartMs)} -{" "}
            {formatTimelineOffsetMs(buffHover.buff.actualEndMs)}
          </span>
          <span>来源：{buffHover.buff.sourceText}</span>
          <div className="timeline-buff-tooltip-list">
            {buffTooltipSegments.map((segment, index) => {
              const effectLines = getBuffSegmentEffectLines(segment, buffHover.tsMsFromStart);
              return (
                <div className="timeline-buff-tooltip-row" key={`${segment.eventKey ?? segment.name}-${index}`}>
                  <span>
                    {segment.name} · {segment.sourceText}
                  </span>
                  <strong>{effectLines.length > 0 ? effectLines.join("，") : segment.eventKey ?? "效果数值未记录"}</strong>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
