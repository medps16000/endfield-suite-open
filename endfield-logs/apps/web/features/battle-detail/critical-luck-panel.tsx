"use client";

import { useEffect, useMemo, useState } from "react";
import type { TimelineEvent } from "../../lib/api/types";
import type { CriticalHit, CriticalLuck } from "../../lib/analysis/critical-luck";

const number = (value: number) => Math.round(value).toLocaleString("zh-CN");
const compact = (value: number) => value >= 1e8 ? `${(value / 1e8).toFixed(2)}亿` : value >= 1e4 ? `${(value / 1e4).toFixed(1)}万` : number(value);

export function CriticalLuckPanel({ events, durationMs, characterName }: {
  events: TimelineEvent[];
  durationMs: number;
  characterName: string | null;
}) {
  const input = useMemo(() => {
    const hits: CriticalHit[] = [];
    let totalDamage = 0;
    let analyzedDamage = 0;
    let totalHits = 0;
    let hasCriticalData = false;
    for (const event of events) {
      if (event.laneType !== "skill" || !event.value || event.value <= 0
        || (characterName && event.sourceCharacterName !== characterName)) continue;
      totalDamage += event.value;
      totalHits += 1;
      const context = event.hitContext?.critical;
      if (!context || typeof context !== "object") continue;
      hasCriticalData = true;
      const row = context as Record<string, unknown>;
      if (row.version !== 1 || row.status !== "verified" || typeof row.isCritical !== "boolean"
        || typeof row.critRate !== "number" || typeof row.critDamageBonus !== "number") continue;
      hits.push({ damage: event.value, isCritical: row.isCritical, critRate: row.critRate, critDamageBonus: row.critDamageBonus,
        uncappedDamage: typeof row.uncappedDamage === "number" ? row.uncappedDamage : undefined,
        damageCap: typeof row.damageCap === "number" ? row.damageCap : null });
      analyzedDamage += event.value;
    }
    return { hits, totalHits, totalDamage, analyzedDamage, hasCriticalData, durationMs };
  }, [events, characterName, durationMs]);
  const [state, setState] = useState<{ input: typeof input; result?: CriticalLuck; error?: string } | null>(null);
  useEffect(() => {
    if (!input.hits.length) return;
    const worker = new Worker(new URL("./critical-luck.worker.ts", import.meta.url));
    worker.onmessage = ({ data }: MessageEvent<{ result?: CriticalLuck; error?: string }>) => setState({ input, ...data });
    worker.onerror = () => setState({ input, error: "本场暴击分布暂时无法计算" });
    worker.postMessage({ hits: input.hits, durationMs: input.durationMs });
    return () => worker.terminate();
  }, [input]);
  const current = state?.input === input ? state : null;
  const result = current?.result;
  const complete = input.hits.length === input.totalHits;
  const coverage = input.totalDamage > 0 ? input.analyzedDamage / input.totalDamage : 0;
  const title = characterName ? `${characterName} · 暴击期望` : "暴击期望";
  const width = 1000, height = 250, left = 35, right = 965, top = 26, bottom = 205;
  const x = (value: number) => result ? left + (value - result.range[0]) / Math.max(1, result.range[1] - result.range[0]) * (right - left) : left;
  const maxProbability = result ? Math.max(...result.points.map((point) => point.probability), 1e-12) : 1;
  const points = result?.points.map((point) => `${x(point.damage)},${bottom - point.probability / maxProbability * (bottom - top)}`).join(" ");
  const topLower = result?.percentileUpper != null ? Math.max(0, 100 * (1 - result.percentileUpper)) : null;
  const topUpper = result?.percentileLower != null ? Math.min(100, 100 * (1 - result.percentileLower)) : null;
  const luckyPercent = topLower !== null && topUpper !== null
    ? topUpper < 1 ? "前 1% 以内" : Math.round(topLower) === Math.round(topUpper) ? `前 ${Math.round(topLower)}%` : `前 ${Math.floor(topLower)}–${Math.ceil(topUpper)}%`
    : null;

  return <section className="panel critical-luck-panel" aria-label={title}>
    <div className="table-toolbar">
      <div><div className="eyebrow">暴击分析</div><h2 style={{ margin: "6px 0 0" }}>{title}</h2></div>
      {input.hits.length > 0 && <span className="muted">{complete ? "全部命中已纳入" : `已覆盖 ${(coverage * 100).toFixed(1)}% 伤害`}</span>}
    </div>
    {!input.hits.length ? <p className="muted critical-luck-empty">{input.totalHits === 0 ? "本场没有可分析的伤害命中。" : input.hasCriticalData
      ? "这条记录的逐次暴击参数尚未确认，当前没有可计算的命中。"
      : "旧记录未保存所需的逐次暴击参数，暂不能显示分析结果。新版客户端会随新战斗上传暴击分析数据。"}</p>
      : current?.error ? <p className="muted critical-luck-empty">{current.error}</p>
      : !result ? <p className="muted critical-luck-empty" role="status">正在计算本场暴击分布…</p>
      : <>
        {!complete && <p className="critical-luck-coverage">以下结果仅对应已确认暴击参数的 {input.hits.length} / {input.totalHits} 次命中。</p>}
        <div className="critical-luck-metrics">
          <div><span>{complete ? "实际总伤" : "实际伤害（已分析）"}</span><strong>{number(result.actualDamage)}</strong></div>
          <div><span>{complete ? "期望总伤" : "期望伤害（已分析）"}</span><strong>{number(result.expectedDamage)}</strong></div>
          <div><span>{complete ? "期望 DPS" : "期望 DPS（已分析）"}</span><strong>{number(result.expectedDps)}</strong></div>
          <div><span>实际偏差</span><strong className={result.damageDifference >= 0 ? "critical-luck-positive" : "critical-luck-negative"}>
            {result.damageDifference >= 0 ? "+" : ""}{(result.relativeDifference * 100).toFixed(2)}%</strong></div>
        </div>
        <p className="critical-luck-description">
          实际伤害比期望{result.damageDifference >= 0 ? "高" : "低"} <strong>{number(Math.abs(result.damageDifference))}</strong>。
          {luckyPercent ? <> 暴击运气位于 <strong className="critical-luck-percentile" title={`百分位数值区间：${(100 * result.percentileLower!).toFixed(2)}%–${(100 * result.percentileUpper!).toFixed(2)}%`}>{luckyPercent}</strong>。</>
            : " 本场纳入的伤害没有暴击随机波动。"}
        </p>
        {result.stochasticHitCount > 0 && <svg className="critical-luck-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`伤害概率分布，期望 ${number(result.expectedDamage)}，实际 ${number(result.actualDamage)}`}>
          <line x1={left} y1={bottom} x2={right} y2={bottom} className="critical-luck-axis" />
          <polygon points={`${left},${bottom} ${points} ${right},${bottom}`} className="critical-luck-area" />
          <polyline points={points} className="critical-luck-curve" />
          <line x1={x(result.expectedDamage)} x2={x(result.expectedDamage)} y1={top} y2={bottom} className="critical-luck-expected" />
          <line x1={x(result.actualDamage)} x2={x(result.actualDamage)} y1={top} y2={bottom} className="critical-luck-actual" />
          <text x={Math.max(left + 34, Math.min(right - 34, x(result.actualDamage)))} y={top - 9} textAnchor="middle" className="critical-luck-tick">实际伤害</text>
          {[0, 1, 2, 3, 4, 5].map((tick) => {
            const value = result.range[0] + (result.range[1] - result.range[0]) * tick / 5;
            return <text key={tick} x={x(value)} y={bottom + 24} textAnchor="middle" className="critical-luck-tick">{compact(value)}</text>;
          })}
        </svg>}
        <div className="critical-luck-legend"><span><i className="critical-luck-legend-expected" />期望伤害</span><span><i className="critical-luck-legend-actual" />实际伤害</span></div>
        <p className="muted critical-luck-note">固定本场实际技能与命中，只计算暴击造成的直接伤害波动；暴击触发的后续攻击与 Buff 按本场记录保留。分布按逐次命中的概率和伤害权重计算，同伤害结果按并列名次处理。</p>
      </>}
  </section>;
}
