/** Conditional on the recorded hits: only each hit's direct critical roll changes. */
export type CriticalHit = {
  damage: number;
  isCritical: boolean;
  critRate: number;
  critDamageBonus: number;
  uncappedDamage?: number;
  damageCap?: number | null;
};

export type CriticalLuck = {
  actualDamage: number;
  expectedDamage: number;
  expectedDps: number;
  damageDifference: number;
  relativeDifference: number;
  standardDeviation: number;
  stochasticHitCount: number;
  percentileLower: number | null;
  percentileUpper: number | null;
  exactDistribution: boolean;
  points: { damage: number; probability: number }[];
  range: [number, number];
  plottedProbability: number;
};

type Roll = { weight: number; probability: number };
type Mass = { value: number; probability: number };

function latticeMass(rolls: Roll[], step: number, rounding: "floor" | "ceil" | "round"): Float64Array {
  const weights = rolls.map((roll) => {
    const units = roll.weight / step;
    const guard = Number.EPSILON * Math.max(1, Math.abs(units)) * 4;
    return rounding === "floor" ? Math.max(0, Math.floor(units - guard))
      : rounding === "ceil" ? Math.ceil(units + guard) : Math.round(units);
  });
  const mass = new Float64Array(weights.reduce((sum, weight) => sum + weight, 0) + 1);
  mass[0] = 1;
  let end = 0;
  for (let index = 0; index < rolls.length; index += 1) {
    const weight = weights[index];
    if (weight === 0) continue;
    const p = rolls[index].probability;
    // Descending updates preserve the previous polynomial's coefficients.
    for (let slot = end + weight; slot >= 0; slot -= 1) {
      mass[slot] = (slot <= end ? mass[slot] * (1 - p) : 0) + (slot >= weight ? mass[slot - weight] * p : 0);
    }
    end += weight;
  }
  return mass;
}

function cumulative(mass: Float64Array, end: number): number {
  let result = 0;
  for (let index = 0; index <= Math.min(end, mass.length - 1); index += 1) result += mass[index];
  return Math.max(0, Math.min(1, result));
}

/** No normal approximation or Monte Carlo. Large supports carry explicit CDF bounds. */
export function computeCriticalLuck(
  hits: readonly CriticalHit[],
  durationMs: number,
  options: { exactLimit?: number; maxBins?: number; workBudget?: number } = {},
): CriticalLuck {
  if (!hits.length || !Number.isFinite(durationMs) || durationMs <= 0) throw new Error("缺少有效命中或战斗时长");
  const rolls: Roll[] = [];
  let actualDamage = 0;
  let fixed = 0;
  let expectedDelta = 0;
  let variance = 0;
  let observedDelta = 0;
  let observedCriticalRolls = 0;
  for (const hit of hits) {
    const { damage, isCritical, critRate: p, critDamageBonus: bonus } = hit;
    if (!Number.isFinite(damage) || damage < 0 || typeof isCritical !== "boolean"
      || !Number.isFinite(p) || p < 0 || p > 1 || !Number.isFinite(bonus) || bonus < 0
      || (p === 0 && isCritical) || (p === 1 && !isCritical)) throw new Error("逐击暴击参数不完整或冲突");
    actualDamage += damage;
    const uncapped = hit.uncappedDamage ?? damage;
    const cap = hit.damageCap ?? Infinity;
    if (!Number.isFinite(uncapped) || uncapped < damage || Number.isNaN(cap) || cap < damage) throw new Error("伤害上限信息冲突");
    const originalBase = isCritical ? uncapped / (1 + bonus) : uncapped;
    const base = Math.min(originalBase, cap);
    const criticalDamage = Math.min(originalBase * (1 + bonus), cap);
    const weight = criticalDamage - base;
    if (!Number.isFinite(weight)) throw new Error("伤害数值超出范围");
    if (p === 0 || weight === 0) { fixed += base; continue; }
    if (p === 1) { fixed += base + weight; continue; }
    fixed += base;
    expectedDelta += weight * p;
    variance += weight * weight * p * (1 - p);
    if (isCritical) { observedDelta += weight; observedCriticalRolls += 1; }
    rolls.push({ weight, probability: p });
  }
  const expectedDamage = fixed + expectedDelta;
  if (rolls.length > 20000) throw new Error("命中数量过多，请选择单个角色查看暴击分布");
  const difference = actualDamage - expectedDamage;
  const summary = {
    actualDamage, expectedDamage, expectedDps: expectedDamage * 1000 / durationMs,
    damageDifference: difference, relativeDifference: expectedDamage > 0 ? difference / expectedDamage : 0,
    standardDeviation: Math.sqrt(variance), stochasticHitCount: rolls.length,
  };
  if (!Number.isFinite(expectedDamage) || !Number.isFinite(variance)) throw new Error("伤害数值超出范围");
  if (!rolls.length) return {
    ...summary, percentileLower: null, percentileUpper: null, exactDistribution: true,
    points: [{ damage: expectedDamage, probability: 1 }], range: [expectedDamage, expectedDamage], plottedProbability: 1,
  };

  const span = rolls.reduce((sum, roll) => sum + roll.weight, 0);
  const epsilon = Number.EPSILON * Math.max(1, span) * (rolls.length + 2) * 4;
  let masses: Mass[];
  let lower: number;
  let upper: number;
  let exact = rolls.length <= (options.exactLimit ?? 16);
  const sameWeight = rolls.every((roll) => roll.weight === rolls[0].weight);
  if (sameWeight) {
    exact = true;
    const mass = latticeMass(rolls, rolls[0].weight, "round");
    masses = [...mass].map((probability, index) => ({ value: index * rolls[0].weight, probability }));
    lower = masses.reduce((sum, item) => sum + (item.value < observedDelta - epsilon ? item.probability
      : Math.abs(item.value - observedDelta) <= epsilon ? item.probability / 2 : 0), 0);
    upper = lower;
  } else if (exact) {
    let states = new Map<number, number>([[0, 1]]);
    for (const { weight, probability: p } of rolls) {
      const next = new Map<number, number>();
      for (const [value, mass] of states) {
        next.set(value, (next.get(value) ?? 0) + mass * (1 - p));
        next.set(value + weight, (next.get(value + weight) ?? 0) + mass * p);
      }
      states = next;
    }
    masses = [...states].map(([value, probability]) => ({ value, probability })).sort((a, b) => a.value - b.value);
    lower = masses.reduce((sum, item) => sum + (item.value < observedDelta - epsilon ? item.probability
      : Math.abs(item.value - observedDelta) <= epsilon ? item.probability / 2 : 0), 0);
    upper = lower;
  } else {
    // floor(w/step) <= w/step <= ceil(w/step) couples all outcomes and gives
    // an interval enclosing the true midrank, including any probability ties.
    const limit = Math.max(128, Math.min(options.maxBins ?? 65536,
      Math.floor((options.workBudget ?? 60_000_000) / (rolls.length * 6))));
    let bins = Math.min(8192, limit);
    let step = span / bins;
    while (true) {
      step = span / bins;
      const floored = latticeMass(rolls, step, "floor");
      const ceiled = latticeMass(rolls, step, "ceil");
      lower = cumulative(ceiled, Math.ceil((observedDelta - epsilon) / step) - 1);
      upper = cumulative(floored, Math.floor((observedDelta + epsilon) / step));
      if (upper - lower <= 0.002 || bins >= limit) break;
      bins = Math.min(limit, bins * 2);
    }
    const rounded = latticeMass(rolls, step, "round");
    masses = [];
    for (let index = 0; index < rounded.length; index += 1) {
      if (rounded[index] > 0) masses.push({ value: index * step, probability: rounded[index] });
    }
    const roundingAllowance = Number.EPSILON * (rolls.length + 2) * 16;
    lower = Math.max(0, lower - roundingAllowance);
    upper = Math.min(1, upper + roundingAllowance);
  }
  // The two endpoint atoms are known exactly even with incommensurate weights.
  if (observedCriticalRolls === 0) {
    lower = Math.exp(rolls.reduce((sum, roll) => sum + Math.log1p(-roll.probability), 0)) / 2;
    upper = lower;
  } else if (observedCriticalRolls === rolls.length) {
    lower = 1 - Math.exp(rolls.reduce((sum, roll) => sum + Math.log(roll.probability), 0)) / 2;
    upper = lower;
  }
  let cumulativeMass = 0;
  let tailLow = masses[0].value;
  let tailHigh = masses[masses.length - 1].value;
  let foundLow = false;
  for (const item of masses) {
    cumulativeMass += item.probability;
    if (!foundLow && cumulativeMass >= 0.0005) { tailLow = item.value; foundLow = true; }
    if (cumulativeMass >= 0.9995) { tailHigh = item.value; break; }
  }
  const low = Math.min(tailLow, expectedDelta, observedDelta);
  const high = Math.max(tailHigh, expectedDelta, observedDelta);
  const width = Math.max(high - low, Math.max(1, expectedDamage) * 1e-9);
  const plotLow = Math.max(0, low - width * 0.06);
  const plotHigh = Math.min(span, high + width * 0.06);
  const bucketWidth = (plotHigh - plotLow) / 96;
  const buckets = new Float64Array(96);
  let plotted = 0;
  for (const item of masses) {
    if (item.value < plotLow || item.value > plotHigh) continue;
    const index = Math.max(0, Math.min(95, Math.floor((item.value - plotLow) / bucketWidth)));
    buckets[index] += item.probability;
    plotted += item.probability;
  }
  return {
    ...summary, percentileLower: lower, percentileUpper: upper, exactDistribution: exact,
    points: [...buckets].map((probability, index) => ({ damage: fixed + plotLow + (index + 0.5) * bucketWidth, probability })),
    range: [fixed + plotLow, fixed + plotHigh], plottedProbability: plotted,
  };
}
