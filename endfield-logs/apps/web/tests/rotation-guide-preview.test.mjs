import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadComponent(source, reactModule = React) {
  const compiled = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const module = { exports: {} };
  // Execute the real component with React's server renderer. CSS is checked in
  // the browser; no DOM, battle clock, or automatic playback is simulated here.
  new Function("require", "module", "exports", compiled)(
    (name) => name.endsWith(".css") ? {} : name === "react" ? reactModule : require(name), module, module.exports);
  return module.exports;
}
const previewSource = await readFile(new URL("../components/rotation-guide-preview.tsx", import.meta.url), "utf8");
const { RotationGuidePreview } = loadComponent(previewSource);
const editor = await readFile(new URL("../components/rotation-editor.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("rotation-editor.tsx", editor, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const presentation = tree.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "stepPresentation");
assert.ok(presentation, "reuse the editor's exact official display mapping");
const { stepPresentation } = loadComponent(`export ${presentation.getText(tree)}`);

function fixture(count = 6) {
  const action = (kind, name, skillName, root) => ({ kind, name, skillName, rootSkillIds: [root], iconUrl: `/images/${kind}.webp` });
  const character = { id: "character-a", name: "庄方宜", avatarUrl: "/images/avatar.png",
    skills: [{ id: "legacy-battle-root", name: "原有惊霆诀", groupId: "official-display-group" }],
    actions: [action("battle_skill", "战技", "惊霆诀", "legacy-battle-root"),
      action("combo_skill", "连携技", "一息万变", "combo-root"),
      action("ultimate_skill", "终结技", "万钧风雷", "ultimate-root")] };
  const catalog = { schemaVersion: 2, characters: [character], maps: [{ id: "map-a", name: "危境遗影·祀", seriesName: "危境再现" }] };
  const draft = { name: "庄方宜 · 开场", dungeonId: "map-a", characterIds: [character.id], enabled: false, displayNotes: "",
    steps: Array.from({ length: count }, (_, index) => ({ id: `step-${index}`, atMs: 10_000 + index * 5_000,
      characterId: character.id, actionKind: ["ultimate_skill", "battle_skill", "combo_skill"][index % 3],
      note: index === 1 ? "先切前台再放战技" : "", phase: index < 3 ? "开场" : "第二轮" })) };
  return { draft, catalog };
}
function freeze(value) {
  if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function render({ draft, catalog, selectedStepId = null }) {
  const artworks = [];
  const html = renderToStaticMarkup(React.createElement(RotationGuidePreview, {
    draft, catalog, selectedStepId, onSelectStep: () => {}, presentStep: stepPresentation,
    renderArtwork: (action, avatarUrl, size) => {
      artworks.push({ action, avatarUrl, size });
      return React.createElement("span", { "data-art": action?.kind || "unknown", "data-avatar": avatarUrl || "", "data-size": size }, action?.name || "技");
    },
  }));
  const window = html.match(/<ol class="rgp-window"[^>]*>([\s\S]*?)<\/ol>/)?.[1] || "";
  const timeline = html.match(/<ol class="rgp-timeline"[^>]*>([\s\S]*?)<\/ol>/)?.[1] || "";
  return { html, window, timeline, artworks };
}

test("the first step previews itself and only the next three without inventing live progress", () => {
  const value = freeze(fixture());
  const { html, window, timeline, artworks } = render(value);
  assert.match(html, /编辑预览/);
  assert.match(html, /按选中步骤预览计划时间/);
  assert.equal((window.match(/<button /g) || []).length, 4);
  assert.match(window, /当前预览：第 1 步/);
  assert.match(window, /后续第 3 步：第 4 步/);
  assert.doesNotMatch(window, /第 5 步/);
  assert.equal((timeline.match(/<button /g) || []).length, 6);
  assert.deepEqual(artworks.slice(0, 4).map((row) => row.size), [72, 52, 52, 52]);
  assert.ok(artworks.slice(0, 4).every((row) => row.avatarUrl === "/images/avatar.png"));
  assert.match(window, /0:10\.000/);
  assert.doesNotMatch(html, /待释放|已完成|倒计时|已命中/);
  assert.match(html, /aria-label="预览上一步" disabled=""|disabled="" aria-label="预览上一步"/);
});

test("selection uses that step's plan time and keeps a long multiline note fully readable", () => {
  const value = fixture();
  const note = `先切前台\n${"保留这段操作说明".repeat(55)}\n最后一个字`;
  value.draft.steps[1].note = note;
  const { html, window } = render(freeze({ ...value, selectedStepId: "step-1" }));
  assert.match(window, /当前预览：第 2 步/);
  assert.match(window, /0:15\.000/);
  assert.match(window, /后续第 3 步：第 5 步/);
  assert.ok(html.includes(`<p>${note}</p>`), "the current note is not shortened, replaced by a tooltip, or dropped");
  assert.match(html, /<span>开场<\/span>/);
});

test("the last step has no invented followers and remains reachable in the full timeline", () => {
  const { html, window, timeline } = render({ ...freeze(fixture()), selectedStepId: "step-5" });
  assert.equal((window.match(/<button /g) || []).length, 1);
  assert.equal((window.match(/class="rgp-remaining"/g) || []).length, 3);
  assert.match(html, /已到排轴最后一步/);
  assert.match(html, /disabled="" aria-label="预览下一步"|aria-label="预览下一步" disabled=""/);
  assert.match(timeline, /预览第 6 步/);
  assert.match(html, /<span>第二轮<\/span>/);
});

test("a short remaining sequence shows only the actual following steps", () => {
  for (const remaining of [2, 3]) {
    const value = freeze(fixture(6));
    const index = value.draft.steps.length - remaining;
    const { window, timeline } = render({ ...value, selectedStepId: value.draft.steps[index].id });
    assert.equal((window.match(/<button /g) || []).length, remaining);
    assert.equal((window.match(/class="rgp-remaining"/g) || []).length, 4 - remaining);
    assert.equal((timeline.match(/<button /g) || []).length, 6);
  }
});

test("a deleted or different plan's selection falls back to the first step without changing the draft", () => {
  const value = freeze(fixture(3));
  const before = JSON.stringify(value.draft);
  const { window, timeline } = render({ ...value, selectedStepId: "deleted-or-other-plan-step" });
  assert.match(window, /当前预览：第 1 步/);
  assert.equal((timeline.match(/aria-current="step"/g) || []).length, 1);
  assert.equal(JSON.stringify(value.draft), before);
});

test("simultaneous steps keep their explicit original sequence", () => {
  const value = fixture(5);
  value.draft.steps.forEach((step, index) => { step.atMs = 2000; step.note = `同秒第${index + 1}个`; });
  const before = structuredClone(value.draft);
  const { window } = render(freeze({ ...value, selectedStepId: "step-1" }));
  const order = [...window.matchAll(/(?:当前预览|后续第 \d 步)：第 (\d) 步/g)].map((match) => Number(match[1]));
  assert.deepEqual(order, [2, 3, 4, 5]);
  assert.equal((window.match(/<time>0:02\.000<\/time>/g) || []).length, 4);
  assert.deepEqual(value.draft, before);
});

test("legacy exact roots reuse the unique official artwork while preserving their exact selector and name", () => {
  const value = fixture(1);
  value.draft.steps[0] = { ...value.draft.steps[0], skillId: "legacy-battle-root" };
  delete value.draft.steps[0].actionKind;
  const before = structuredClone(value.draft);
  const { window, artworks } = render(freeze(value));
  assert.match(window, /原有惊霆诀/);
  assert.equal(artworks[0].action, value.catalog.characters[0].actions[0]);
  assert.deepEqual(value.draft, before);
  assert.equal("actionKind" in value.draft.steps[0], false);
  assert.doesNotMatch(window, /legacy-battle-root|official-display-group/);
});

test("unknown, ambiguous, or mixed legacy selectors never choose a convenient first artwork", () => {
  for (const mode of ["unknown", "ambiguous", "mixed"]) {
    const value = fixture(1);
    const step = value.draft.steps[0];
    delete step.actionKind;
    step.skillId = mode === "unknown" ? "not-an-official-root" : "legacy-battle-root";
    if (mode === "ambiguous") value.catalog.characters[0].actions[1].rootSkillIds.push(step.skillId);
    if (mode === "mixed") step.actionKind = "battle_skill";
    const before = structuredClone(value.draft);
    const { artworks, window } = render(freeze(value));
    assert.equal(artworks[0].action, undefined, mode);
    assert.deepEqual(value.draft, before, mode);
    assert.doesNotMatch(window, /not-an-official-root|legacy-battle-root/);
  }
});

test("an empty plan, missing catalog, and incomplete times remain honest editing states", () => {
  const empty = render({ ...freeze(fixture(0)), catalog: null });
  assert.match(empty.html, /还没有技能步骤/);
  assert.equal(empty.window, "");
  assert.equal(empty.timeline, "");
  for (const atMs of [Number.NaN, Number.POSITIVE_INFINITY, -1, 1.5, 3_600_001]) {
    const value = fixture(1);
    value.draft.steps[0].atMs = atMs;
    const { html, window, artworks } = render(freeze({ ...value, catalog: null }));
    assert.match(window, /时间待填/);
    assert.match(window, /选择角色/);
    assert.match(window, /选择动作/);
    assert.match(html, /尚未选择地图/);
    assert.equal(artworks[0].action, undefined);
    assert.doesNotMatch(html, /NaN|Infinity|character-a|map-a/);
  }
});

test("all 500 steps remain selectable when previewing the final step", () => {
  const value = freeze(fixture(500));
  const before = JSON.stringify(value.draft);
  const { html, window, timeline } = render({ ...value, selectedStepId: "step-499" });
  assert.equal((timeline.match(/<button /g) || []).length, 500);
  assert.equal((window.match(/<button /g) || []).length, 1);
  assert.match(window, /当前预览：第 500 步/);
  assert.match(timeline, /预览第 1 步/);
  assert.match(timeline, /预览第 500 步/);
  assert.match(html, /<strong>500<\/strong> \/ 500/);
  assert.equal(JSON.stringify(value.draft), before);
});

test("moving the same selected identity reruns its real scroll effect without scrolling the page or changing the draft", () => {
  // SSR cannot run effects. This separate hook harness executes the actual
  // callback with explicit geometry; viewport/keyboard QA remains a browser check.
  let effects = [];
  let selectedBounds = { left: 50, right: 120, width: 70 };
  const container = {
    scrollLeft: 0,
    getBoundingClientRect: () => ({ left: 0, right: 160, width: 160 }),
    querySelector: (selector) => {
      assert.equal(selector, '[aria-current="step"]');
      return { getBoundingClientRect: () => selectedBounds };
    },
  };
  const { RotationGuidePreview: HookPreview } = loadComponent(previewSource, { ...React,
    useRef: () => ({ current: container }), useEffect: (callback, dependencies) => effects.push({ callback, dependencies }),
  });
  const props = { ...fixture(500), selectedStepId: "step-249", onSelectStep: () => {}, presentStep: stepPresentation,
    renderArtwork: () => null };
  HookPreview(freeze(props));
  assert.equal(effects.length, 1);
  const first = effects[0];
  first.callback();
  assert.equal(container.scrollLeft, 0, "already visible selection should not shift the timeline");
  const moved = structuredClone({ draft: props.draft, catalog: props.catalog });
  const selected = moved.draft.steps.splice(249, 1)[0];
  moved.draft.steps.push(selected);
  const before = JSON.stringify(moved.draft);
  effects = [];
  HookPreview(freeze({ ...props, ...moved }));
  const second = effects[0];
  assert.equal(second.dependencies[0], first.dependencies[0], "the selected step identity is unchanged");
  assert.ok(second.dependencies.some((value, index) => !Object.is(value, first.dependencies[index])),
    "a changed original step position must reschedule visibility even with the same identity");
  selectedBounds = { left: 35000, right: 35070, width: 70 };
  second.callback();
  assert.equal(container.scrollLeft, 34955);
  assert.equal(JSON.stringify(moved.draft), before);
});

test("untrusted notes and names are rendered as text rather than executable HTML", () => {
  const value = fixture(1);
  value.draft.name = '<img src=x onerror="alert(1)">';
  value.draft.steps[0].note = "<script>alert(2)</script>\n保留换行";
  const { html } = render(freeze(value));
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /&lt;script&gt;alert\(2\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|<img src=x/);
});
