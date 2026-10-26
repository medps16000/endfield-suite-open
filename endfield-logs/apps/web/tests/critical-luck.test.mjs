import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/analysis/critical-luck.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
const { computeCriticalLuck } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const hit = (base, critical, p = 0.2, c = 0.5) => ({ damage: base * (critical ? 1 + c : 1), isCritical: critical, critRate: p, critDamageBonus: c });
const close = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

test("one roll has exact mean, variance and tie-aware percentile", () => {
  const normal = computeCriticalLuck([hit(100, false)], 1000);
  close(normal.expectedDamage, 110);
  close(normal.expectedDps, 110);
  close(normal.standardDeviation, 20);
  close(normal.percentileLower, 0.4);
  close(normal.percentileUpper, 0.4);
  close(computeCriticalLuck([hit(100, true)], 1000).percentileLower, 0.9);
});

test("large hits count by damage weight, not by observed critical hit rate", () => {
  const result = computeCriticalLuck([hit(100, false, 0.5), hit(1000, true, 0.5)], 2000);
  close(result.actualDamage, 1600);
  close(result.expectedDamage, 1375);
  close(result.percentileLower, 0.625);
  close(result.damageDifference, 225);
});

test("guaranteed and impossible critical rolls have no luck percentile", () => {
  const result = computeCriticalLuck([hit(100, false, 0), hit(100, true, 1)], 1000);
  close(result.expectedDamage, 250);
  close(result.standardDeviation, 0);
  assert.equal(result.percentileLower, null);
  assert.equal(result.points[0].probability, 1);
});

test("equal weights use an exact Poisson-binomial distribution, including ties", () => {
  const hits = Array.from({ length: 20 }, (_, i) => hit(100, i < 4));
  const result = computeCriticalLuck(hits, 1000);
  let probability = 0.8 ** 20;
  let midrank = 0;
  for (let k = 0; k <= 4; k += 1) {
    midrank += probability * (k === 4 ? 0.5 : 1);
    probability *= ((20 - k) / (k + 1)) * (0.2 / 0.8);
  }
  assert.equal(result.exactDistribution, true);
  close(result.percentileLower, midrank);
  close(result.expectedDamage, 2200);
});

test("bounded numerical convolution encloses exhaustive weighted-roll results", () => {
  for (let sample = 0; sample < 8; sample += 1) {
    const hits = Array.from({ length: 12 }, (_, i) => hit(Math.sqrt(2 + i) * 73, (i + sample) % 4 === 0, 0.05 + (i % 5) / 10));
    const exact = computeCriticalLuck(hits, 1000, { exactLimit: 16 });
    const bounded = computeCriticalLuck(hits, 1000, { exactLimit: 0, maxBins: 8192 });
    assert.ok(bounded.percentileLower <= exact.percentileLower + 1e-12);
    assert.ok(bounded.percentileUpper >= exact.percentileUpper - 1e-12);
    close(bounded.expectedDamage, exact.expectedDamage);
    close(bounded.standardDeviation, exact.standardDeviation);
    assert.ok(bounded.plottedProbability >= 0.998);
  }
});

test("invalid probabilities and contradictory observations are rejected", () => {
  for (const invalid of [hit(100, true, 0), hit(100, false, 1), hit(100, true, 1.1), hit(100, true, NaN), hit(Infinity, false)]) {
    assert.throws(() => computeCriticalLuck([invalid], 1000));
  }
});

test("overkill caps both counterfactual outcomes before measuring luck", () => {
  const result = computeCriticalLuck([{ ...hit(100, true), damage: 50, uncappedDamage: 150, damageCap: 50 }], 1000);
  assert.equal(result.expectedDamage, 50);
  assert.equal(result.standardDeviation, 0);
  assert.equal(result.percentileLower, null);
});

test("zero critical hits among many unequal weights has an exact endpoint percentile", () => {
  const result = computeCriticalLuck(Array.from({ length: 25 }, (_, i) => hit(150 + i * 11, false, 0.05)), 1000);
  close(result.percentileLower, 0.95 ** 25 / 2);
  close(result.percentileUpper, result.percentileLower);
});

test("a possible critical killing blow is clipped to observed pre-hit HP", () => {
  const result = computeCriticalLuck([{ ...hit(100, false), uncappedDamage: 100, damageCap: 120 }], 1000);
  close(result.expectedDamage, 104);
  close(result.standardDeviation, 8);
});
