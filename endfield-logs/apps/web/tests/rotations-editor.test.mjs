import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const compile = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const moduleUrl = (source) => `data:text/javascript;base64,${Buffer.from(compile(source)).toString("base64")}`;
const source = await readFile(new URL("../components/rotation-editor.tsx", import.meta.url), "utf8");
const tree = ts.createSourceFile("rotation-editor.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function nodeMatching(predicate) {
  let result;
  const visit = (node) => { if (!result && predicate(node)) result = node; if (!result) ts.forEachChild(node, visit); };
  visit(tree);
  assert.ok(result, "the production editor path being tested must exist");
  return result;
}
const method = (name) => nodeMatching((node) => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(tree);
const initialValue = (name) => nodeMatching((node) => ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name)
  && node.name.elements[0].getText(tree) === name).initializer.arguments[0].getText(tree);
const empty = nodeMatching((node) => ts.isVariableDeclaration(node) && node.name.getText(tree) === "EMPTY_DRAFT").initializer.getText(tree);
const apiClient = await readFile(new URL("../lib/api/client.ts", import.meta.url), "utf8");
const api = await readFile(new URL("../lib/api/rotations.ts", import.meta.url), "utf8");
const apiUrl = moduleUrl(api.replace('"./client"', JSON.stringify(moduleUrl(apiClient))));
const shareEffect = nodeMatching((node) => ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
  && node.arguments[0]?.getText(tree).includes("shareAttempt.current += 1;")).arguments[0].getText(tree);
const focusEffect = nodeMatching((node) => ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
  && node.arguments[0]?.getText(tree).includes("if (!validationFocus)")).arguments[0].getText(tree);
const timeEffect = nodeMatching((node) => ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
  && node.arguments[0]?.getText(tree).includes("Object.is(atMs, lastEmitted.current)")).arguments[0].getText(tree);
const timeChange = nodeMatching((node) => ts.isArrowFunction(node) && ts.isJsxExpression(node.parent)
  && node.body.getText(tree).includes("lastEmitted.current = parsed;")).getText(tree);
const openDraftCallback = nodeMatching((node) => ts.isVariableDeclaration(node) && node.name.getText(tree) === "openDraft")
  .initializer.arguments[0].getText(tree);
// Execute the actual editor function bodies with bounded state/network spies.
// This verifies its decisions without claiming DOM or browser coverage.
const paths = await import(moduleUrl(`
  import { rotationBody, RotationApiError, serializeRotationExport } from ${JSON.stringify(apiUrl)};
  export { serializeRotationExport };
  const EMPTY_DRAFT = ${empty};
  ${method("copyDraft")}
  ${method("ordered")}
  ${method("checkOwner")}
  ${method("seconds")}
  export ${method("validationIssues")}
  export ${method("validationError")}
  export ${method("stepPresentation")}
  export function initialDraft() { return {draft: ${initialValue("draft")}, saved: ${initialValue("savedDraft")}}; }
  export function cloneDraft(draft) { return copyDraft(draft); }
  export function locate(context, issue) {
    const {draft, setPreviewStep, setValidationFocus} = context;
    ${method("focusIssue")}
    return focusIssue(issue);
  }
  export function performFocus(context) {
    const {validationFocus, currentStep, draft, workshop, setValidationFocus} = context;
    return (${focusEffect})();
  }
  export async function share(context) {
    const {busy, catalog, unauthorized, draft, accountId, shareAttempt, shareContext, navigator,
      setError, focusIssue, setShareText, setShareHelp} = context;
    ${method("copyShareContent")}
    return copyShareContent();
  }
  export function shareLifecycle(context) {
    const {shareAttempt, setShareText, setShareHelp} = context;
    return (${shareEffect})();
  }
  export function editTime(context, value) {
    const {setText, lastEmitted, onChange, rawTexts, stepId} = context;
    return (${timeChange})({target:{value}});
  }
  export function mountTime({atMs, rawTexts, stepId}) { return ${initialValue("text")}; }
  export function syncTime(context) {
    const {atMs, setText, lastEmitted, rawTexts, stepId} = context;
    return (${timeEffect})();
  }
  export function reopenDraft(context, ...args) {
    const {stepTimeTexts, shareAttempt, setShareText, setShareHelp, setSelected, setDraft, setSavedDraft,
      setPreviewStep, setCharacterSearch, setConflict, setError, setValidationFocus, setShowAllIssues} = context;
    return (${openDraftCallback})(...args);
  }
  export async function reorder(context, index, direction) {
    const {plans, selected, dirty, savedDraft, accountId, rotationRequest, setBusy, setError,
      setNotice, setPlans, setSelected, setConflict, openDraft, reportError} = context;
    ${method("movePlan")}
    return movePlan(index, direction);
  }
  export function copy(context) {
    const {catalog, draft, setNotice, setError, openDraft, crypto} = context;
    ${method("duplicate")}
    return duplicate();
  }
  export function insertStep(context, ...args) {
    const {busy, catalog, unauthorized, draft, currentStep, currentIndex, setDraft,
      setPreviewStep, crypto} = context;
    ${method("addStep")}
    return addStep(...args);
  }
  export function unauthorizedNew() {
    const unauthorized = true;
    ${method("newPlan")}
    return newPlan();
  }
  export function unauthorizedImport() {
    const unauthorized = true, catalog = {};
    ${method("importPlan")}
    return importPlan();
  }
`));

const plan = (id, position, revision = 1) => ({ id, name: id, dungeonId: "indie_hard026_s", characterIds: ["a", "b", "c", "d"], enabled: true,
  position, revision, updatedAt: "2026-10-15T00:00:00Z", displayNotes: "",
  steps: [{ id: "step-1", atMs: 1000, characterId: "a", skillId: "root-a", note: "saved note", phase: "opening" }] });
const body = ({name, dungeonId, characterIds, enabled, steps, displayNotes}) => ({ name, dungeonId, characterIds, enabled, displayNotes, steps });
function reorderContext({ dirty = true, remoteNote = "saved note" } = {}) {
  const original = plan("axis-a", 0, 4);
  const other = plan("axis-b", 1, 1);
  const replacement = { ...original, position: 1, revision: 5,
    steps: [{ ...original.steps[0], note: remoteNote }] };
  const state = { selected: original, draft: { ...body(original), steps: [{ ...original.steps[0], note: dirty ? "local unsaved note" : "saved note" }] },
    plans: [original, other], busy: false, error: "", notice: "", conflict: false, openCalls: 0, requests: [] };
  const context = { plans: state.plans, selected: original, dirty, savedDraft: JSON.stringify(body(original)), accountId: "account-a",
    rotationRequest: async (path, init, owner) => {
      state.requests.push({ path, body: JSON.parse(init.body), owner });
      return { schemaVersion: 1, accountId: "account-a", revision: "new-envelope", rotations: [{ ...other, position: 0, revision: 2 }, replacement] };
    },
    setBusy: (value) => { state.busy = value; }, setError: (value) => { state.error = value; },
    setNotice: (value) => { state.notice = value; }, setPlans: (value) => { state.plans = value; },
    setSelected: (value) => { state.selected = value; }, setConflict: (value) => { state.conflict = value; },
    openDraft: (value) => { state.openCalls += 1; state.selected = value; state.draft = body(value); },
    reportError: (cause) => { throw cause; },
  };
  return { context, state };
}

function stepFixture({ steps, selectedId, busy = false, unauthorized = false, catalogAvailable = true } = {}) {
  const original = body(plan("step-fixture", 0));
  original.steps = steps ?? [
    { ...original.steps[0], id: "existing-a", atMs: 1234, note: "切到前台", phase: "开场" },
    { id: "existing-b", atMs: 1234, characterId: "b", actionKind: "battle_skill", note: "保持同秒原顺序", phase: "开场" },
    { id: "existing-c", atMs: 9000, characterId: "c", actionKind: "ultimate_skill", note: "后续步骤", phase: "第二轮" },
  ];
  const catalog = { maps: [{id: original.dungeonId}], characters: [...original.characterIds, "outsider"].map((id) => ({id,
    skills: [{id: `root-${id}`}, {id: `ultimate-${id}`}], actions: [
      {kind: "battle_skill", rootSkillIds: [`root-${id}`]},
      {kind: "ultimate_skill", rootSkillIds: [`ultimate-${id}`]},
    ]})) };
  const state = { draft: original, selectedId: selectedId === undefined ? original.steps[0]?.id : selectedId,
    writes: 0, selectionWrites: 0, uuidCalls: 0 };
  return {state, catalog, context() {
    const currentIndex = state.draft.steps.findIndex((step) => step.id === state.selectedId);
    return {busy, unauthorized, catalog: catalogAvailable ? catalog : null, draft: state.draft,
      currentIndex, currentStep: state.draft.steps[currentIndex],
      setDraft: (update) => { state.writes += 1; state.draft = typeof update === "function" ? update(state.draft) : update; },
      setPreviewStep: (id) => { state.selectionWrites += 1; state.selectedId = id; },
      crypto: { randomUUID: () => `new-step-${++state.uuidCalls}` }};
  }};
}

function presentationFixture() {
  const owner = "chr_0030_zhuangfy";
  return {id: owner, name: "庄方宜", avatarUrl: "/images/character/fixture-avatar.png", skills: [
    {id: `${owner}_normal_skill`, name: "惊霆诀", groupId: "normal-display-group"},
    {id: `${owner}_normal_skill_ult`, name: "旧版强化惊霆诀", groupId: "normal-display-group"},
    {id: `${owner}_ultimate_skill`, name: "万钧风雷", groupId: "ultimate-display-group"},
    // Display grouping/name does not make an enhanced attack an R cast.
    {id: `${owner}_attack1_ult`, name: "万钧风雷", groupId: "ultimate-display-group"},
  ], actions: [
    {kind: "battle_skill", name: "战技", skillName: "惊霆诀", iconUrl: "/images/character/game-skills/fixture-battle.webp",
      rootSkillIds: [`${owner}_normal_skill`, `${owner}_normal_skill_ult`]},
    {kind: "ultimate_skill", name: "终结技", skillName: "万钧风雷", iconUrl: "/images/character/game-skills/fixture-ultimate.webp",
      rootSkillIds: [`${owner}_ultimate_skill`]},
  ]};
}

test("an old exact step gains only its uniquely declared action artwork and keeps the old Chinese skill name", () => {
  for (const [root, kind, expectedName] of [
    ["chr_0030_zhuangfy_normal_skill_ult", "battle_skill", "旧版强化惊霆诀"],
    ["chr_0030_zhuangfy_ultimate_skill", "ultimate_skill", "万钧风雷"],
  ]) {
    const character = presentationFixture();
    const step = Object.freeze({id: "old-step", characterId: character.id, skillId: root,
      atMs: 10000, phase: "暂停后", note: "原操作备注"});
    const before = structuredClone(step);
    const expectedAction = character.actions.find((action) => action.kind === kind);
    const display = paths.stepPresentation(step, character);
    assert.deepEqual(display.action, expectedAction);
    assert.equal(display.action.iconUrl, expectedAction.iconUrl);
    assert.equal(display.kindName, expectedAction.name);
    assert.equal(display.name, expectedName);
    assert.deepEqual(step, before);
    assert.equal(Object.hasOwn(step, "actionKind"), false);
  }
});

test("same skill name and display group never authorize an old exact root to borrow a different action icon", () => {
  const character = presentationFixture();
  const step = Object.freeze({id: "enhanced-attack", characterId: character.id,
    skillId: "chr_0030_zhuangfy_attack1_ult", atMs: 5000, phase: "", note: ""});
  const exact = character.skills.find((skill) => skill.id === step.skillId);
  assert.equal(exact.name, character.actions[1].skillName);
  const display = paths.stepPresentation(step, character);
  assert.equal(display.action, undefined);
  assert.equal(display.kindName, "原有技能");
  assert.equal(display.name, exact.name);
});

test("unknown roots and roots explicitly shared across actions remain without an inferred action icon", () => {
  const character = presentationFixture();
  const step = Object.freeze({id: "unknown-root", characterId: character.id,
    skillId: "chr_0030_zhuangfy_ultimate_skill_unknown", atMs: 0, phase: "", note: ""});
  assert.equal(paths.stepPresentation(step, character).action, undefined);
  assert.equal(paths.stepPresentation(step, undefined).action, undefined);
  const ambiguous = presentationFixture();
  const root = "chr_0030_zhuangfy_normal_skill_ult";
  ambiguous.actions[1].rootSkillIds.push(root);
  const old = Object.freeze({...step, id: "ambiguous-root", skillId: root});
  const before = structuredClone(old);
  for (const actions of [ambiguous.actions, [...ambiguous.actions].reverse()]) {
    const display = paths.stepPresentation(old, {...ambiguous, actions});
    assert.equal(display.action, undefined);
    assert.equal(display.kindName, "原有技能");
    assert.equal(display.name, "旧版强化惊霆诀");
    assert.deepEqual(old, before);
  }
});

test("ordinary actionKind steps continue to use their selected official action presentation", () => {
  const character = presentationFixture();
  for (const action of character.actions) {
    const step = Object.freeze({id: `new-${action.kind}`, characterId: character.id,
      actionKind: action.kind, atMs: 15000, phase: "", note: ""});
    const before = structuredClone(step);
    const display = paths.stepPresentation(step, character);
    assert.deepEqual(display.action, action);
    assert.equal(display.kindName, action.name);
    assert.equal(display.name, action.skillName);
    assert.deepEqual(step, before);
    assert.equal(Object.hasOwn(step, "skillId"), false);
  }
});

test("mixed or absent selectors cannot make an invalid step look like a valid action", () => {
  const character = presentationFixture();
  for (const selection of [
    {skillId: "chr_0030_zhuangfy_normal_skill_ult", actionKind: "battle_skill"},
    {skillId: "chr_0030_zhuangfy_normal_skill_ult", actionKind: "ultimate_skill"},
    {}, {actionKind: "unknown_action"},
  ]) {
    const step = Object.freeze({id: "invalid-selection", characterId: character.id,
      atMs: 0, phase: "", note: "", ...selection});
    const before = structuredClone(step);
    const display = paths.stepPresentation(step, character);
    assert.equal(display.action, undefined);
    assert.equal(display.kindName, "待选动作");
    assert.deepEqual(step, before);
  }
});

test("a character action inserts after the selected step without changing same-time order or guessing a delay", () => {
  const fixture = stepFixture();
  const before = structuredClone(fixture.state.draft);
  paths.insertStep(fixture.context(), "b", "ultimate_skill");
  const {draft, selectedId} = fixture.state;
  assert.deepEqual(draft.steps.map((step) => step.id), ["existing-a", "new-step-1", "existing-b", "existing-c"]);
  assert.deepEqual(draft.steps.filter((step) => step.id !== selectedId), before.steps);
  const inserted = draft.steps.find((step) => step.id === selectedId);
  assert.deepEqual(inserted, {id: "new-step-1", atMs: 1234, characterId: "b", actionKind: "ultimate_skill", phase: "开场", note: ""});
  assert.equal(paths.validationError(draft, fixture.catalog), null);
  assert.deepEqual({...draft, steps: before.steps}, before);
});

test("blank steps inherit only explicit editing context, while consecutive insertions receive distinct identities", () => {
  const fixture = stepFixture();
  paths.insertStep(fixture.context());
  const blank = fixture.state.draft.steps[1];
  assert.equal(blank.id, fixture.state.selectedId);
  assert.equal(blank.atMs, 1234);
  assert.equal(blank.characterId, "a");
  assert.equal(blank.phase, "开场");
  assert.equal(blank.note, "");
  assert.equal(blank.skillId, undefined);
  assert.equal(blank.actionKind, undefined);
  // The author must choose an action; an old exact root is not silently reused.
  assert.match(paths.validationError(fixture.state.draft, fixture.catalog), /对应动作/);
  paths.insertStep(fixture.context(), "a", "battle_skill");
  assert.equal(fixture.state.selectedId, "new-step-2");
  assert.deepEqual(fixture.state.draft.steps.map((step) => step.id), [
    "existing-a", "new-step-1", "new-step-2", "existing-b", "existing-c",
  ]);
  assert.equal(fixture.state.draft.steps[2].atMs, blank.atMs);
});

test("copying either an old exact root or a new action preserves its meaning and changes only the step identity", () => {
  for (const selectedId of ["existing-a", "existing-c"]) {
    const fixture = stepFixture({selectedId});
    const original = structuredClone(fixture.state.draft.steps.find((step) => step.id === selectedId));
    const before = structuredClone(fixture.state.draft);
    const index = fixture.state.draft.steps.findIndex((step) => step.id === selectedId);
    paths.insertStep(fixture.context(), undefined, undefined, true);
    const copied = fixture.state.draft.steps[index + 1];
    assert.notEqual(copied.id, original.id);
    assert.deepEqual({...copied, id: original.id}, original);
    assert.deepEqual(fixture.state.draft.steps.filter((step) => step.id !== copied.id), before.steps);
    assert.equal(fixture.state.selectedId, copied.id);
    assert.equal(paths.validationError(fixture.state.draft, fixture.catalog), null);
    assert.equal(Number(!!copied.skillId) + Number(!!copied.actionKind), 1);
  }
});

test("an empty axis starts explicitly at zero, and an unselected insertion appends without rewriting existing steps", () => {
  const emptyAxis = stepFixture({steps: [], selectedId: null});
  paths.insertStep(emptyAxis.context(), "d", "battle_skill");
  assert.deepEqual(emptyAxis.state.draft.steps, [
    {id: "new-step-1", atMs: 0, characterId: "d", actionKind: "battle_skill", phase: "", note: ""},
  ]);
  assert.equal(paths.validationError(emptyAxis.state.draft, emptyAxis.catalog), null);
  const unselected = stepFixture({selectedId: null});
  const before = structuredClone(unselected.state.draft.steps);
  paths.insertStep(unselected.context(), "b", "battle_skill");
  assert.deepEqual(unselected.state.draft.steps.slice(0, before.length), before);
  assert.equal(unselected.state.draft.steps.at(-1).id, unselected.state.selectedId);
});

test("disabled, incomplete and full editors refuse insertion without mutating the draft or consuming a UUID", () => {
  const full = Array.from({length: 500}, (_, index) => ({id: `full-${index}`, atMs: index * 1000,
    characterId: "a", actionKind: "battle_skill", phase: "", note: ""}));
  const cases = [stepFixture({busy: true}), stepFixture({unauthorized: true}),
    stepFixture({catalogAvailable: false}), stepFixture({steps: full})];
  const incomplete = stepFixture(); incomplete.state.draft.characterIds.pop(); cases.push(incomplete);
  for (const fixture of cases) {
    const before = structuredClone(fixture.state);
    for (const args of [[], ["a", "battle_skill"], [undefined, undefined, true]]) {
      paths.insertStep(fixture.context(), ...args);
      assert.deepEqual(fixture.state, before);
    }
  }
});

test("the final allowed step reaches 500, then every add mode refuses another step", () => {
  const steps = Array.from({length: 499}, (_, index) => ({id: `limit-${index}`, atMs: index * 1000,
    characterId: "a", actionKind: "battle_skill", phase: "末段", note: ""}));
  const fixture = stepFixture({steps, selectedId: "limit-498"});
  paths.insertStep(fixture.context(), "b", "ultimate_skill");
  assert.equal(fixture.state.draft.steps.length, 500);
  assert.equal(fixture.state.draft.steps.at(-1).atMs, 498000);
  assert.equal(fixture.state.draft.steps.at(-1).phase, "末段");
  assert.equal(paths.validationError(fixture.state.draft, fixture.catalog), null);
  const before = structuredClone(fixture.state);
  for (const args of [[], ["a", "battle_skill"], [undefined, undefined, true]]) {
    paths.insertStep(fixture.context(), ...args);
    assert.deepEqual(fixture.state, before);
  }
});

test("shortcut insertion rejects unavailable character actions rather than picking a replacement", () => {
  const attempts = [
    ["outsider", "battle_skill"], ["missing", "battle_skill"], ["a", "combo_skill"],
    ["a", "normal_attack"], ["a", undefined], [undefined, "battle_skill"],
  ];
  for (const args of attempts) {
    const fixture = stepFixture();
    const before = structuredClone(fixture.state);
    paths.insertStep(fixture.context(), ...args);
    assert.deepEqual(fixture.state, before);
  }
  const noRoots = stepFixture(); noRoots.catalog.characters[0].actions[0].rootSkillIds = [];
  const before = structuredClone(noRoots.state);
  paths.insertStep(noRoots.context(), "a", "battle_skill");
  assert.deepEqual(noRoots.state, before);
  const noSelection = stepFixture({steps: [], selectedId: null});
  const emptyBefore = structuredClone(noSelection.state);
  paths.insertStep(noSelection.context(), undefined, undefined, true);
  assert.deepEqual(noSelection.state, emptyBefore);
});

test("an untouched empty-account draft starts clean", () => {
  const initial = paths.initialDraft();
  assert.equal(JSON.stringify(initial.draft), initial.saved);
});

test("saving accepts a known character action or an exact old step, and refuses ambiguous selections", () => {
  const original = body(plan("axis-a", 0));
  const catalog = { maps: [{id: original.dungeonId}], characters: original.characterIds.map((id) => ({id,
    skills: [{id: `root-${id}`}], actions: [{kind: "battle_skill", rootSkillIds: [`root-${id}`]}]})) };
  assert.equal(paths.validationError(original, catalog), null);
  const step = original.steps[0];
  const {skillId, ...withoutSkill} = step;
  const actionPlan = {...original, steps: [{...withoutSkill, actionKind: "battle_skill"}]};
  assert.equal(paths.validationError(actionPlan, catalog), null);
  for (const invalid of [{...step, actionKind: "battle_skill"}, {...withoutSkill}, {...withoutSkill, actionKind: "ultimate_skill"},
    {...withoutSkill, actionKind: "battle_skill", characterId: "outsider"}, {...step, skillId: "unknown"}]) {
    assert.match(paths.validationError({...original, steps: [invalid]}, catalog), /对应动作/);
  }
  catalog.characters[0].actions[0].rootSkillIds = [];
  assert.match(paths.validationError(actionPlan, catalog), /对应动作/);
});

test("structured issues collect exact field and step identities without mutating NaN or old selectors", () => {
  const {state, catalog} = stepFixture();
  const draft = {...state.draft, name: "", dungeonId: "not-ranked", displayNotes: "长".repeat(2001),
    steps: state.draft.steps.map((step, index) => index === 1 ? {...step, atMs: Number.NaN, note: "长".repeat(501), phase: "长".repeat(81)} : step)};
  const before = structuredClone(draft);
  const issues = paths.validationIssues(draft, catalog);
  assert.deepEqual(issues.map((issue) => issue.field), ["name", "displayNotes", "dungeonId", "stepTime", "stepNote", "stepPhase"]);
  for (const issue of issues.filter((issue) => issue.stepIndex !== undefined)) {
    assert.equal(issue.stepIndex, 1); assert.equal(issue.stepId, "existing-b");
  }
  assert.equal(paths.validationError(draft, catalog), issues[0].message);
  assert.deepEqual(draft, before);
  assert.ok(Number.isNaN(paths.cloneDraft(draft).steps[1].atMs));
  assert.equal(paths.cloneDraft(draft).steps[0].skillId, "root-a");
  assert.equal(Object.hasOwn(paths.cloneDraft(draft).steps[0], "actionKind"), false);
});

test("time validation rejects every invalid number while zero and equal-time order stay valid", () => {
  const {state, catalog} = stepFixture();
  const base = {...state.draft, steps: [state.draft.steps[0]]};
  for (const atMs of [Number.NaN, Infinity, -Infinity, -1, 0.5, 3_600_001]) {
    assert.ok(paths.validationIssues({...base, steps: [{...base.steps[0], atMs}]}, catalog).some((issue) => issue.code === "step_time"));
  }
  for (const atMs of [0, 3_600_000]) assert.deepEqual(paths.validationIssues({...base, steps: [{...base.steps[0], atMs}]}, catalog), []);
  const equal = {...state.draft, steps: state.draft.steps.map((step) => ({...step, atMs: 0}))};
  assert.deepEqual(paths.validationIssues(equal, catalog), []);
  const outOfOrder = {...state.draft, steps: state.draft.steps.map((step, index) => ({...step, atMs: index === 0 ? 2000 : 1000}))};
  assert.deepEqual(paths.validationIssues(outOfOrder, catalog).map((issue) => [issue.code, issue.stepIndex]), [["step_order", 1]]);
});

test("time input retains an invalid or empty original string when its emitted NaN returns through a render", () => {
  const lastEmitted = {current: 1000};
  const state = {text: "1", value: 1000};
  const context = {lastEmitted, rawTexts: new Map(), stepId: "first", setText: (value) => { state.text = value; }, onChange: (value) => { state.value = value; }};
  for (const original of ["", "1..2", "1.2345", "-1", "Infinity", "未填"]) {
    paths.editTime(context, original);
    assert.ok(Number.isNaN(state.value));
    paths.syncTime({...context, atMs: state.value});
    assert.equal(state.text, original);
  }
  paths.editTime(context, "1.234");
  assert.equal(state.value, 1234);
  paths.syncTime({...context, atMs: 2000});
  assert.equal(state.text, "2");
});

test("invalid raw time survives switching to another step and exact issue navigation without leaking into that step", () => {
  const {state, catalog} = stepFixture();
  const rawTexts = new Map();
  const first = state.draft.steps[0], third = state.draft.steps[2];
  const firstContext = {stepId: first.id, rawTexts, lastEmitted: {current:first.atMs},
    setText: () => {}, onChange: (value) => {first.atMs = value;}};
  paths.editTime(firstContext, "1.2345");
  assert.ok(Number.isNaN(first.atMs));
  assert.equal(paths.mountTime({atMs:third.atMs, rawTexts, stepId:third.id}), "9");
  paths.editTime({stepId:third.id, rawTexts, lastEmitted:{current:third.atMs}, setText:()=>{},
    onChange:(value)=>{third.atMs=value;}}, "7.001");
  const issue = paths.validationIssues(state.draft, catalog).find((item) => item.field === "stepTime");
  const selected = [];
  paths.locate({draft:state.draft, setPreviewStep:(value)=>selected.push(value), setValidationFocus:()=>{}},issue);
  assert.deepEqual(selected,[first.id]);
  assert.equal(paths.mountTime({atMs:first.atMs, rawTexts, stepId:first.id}), "1.2345");
  assert.equal(paths.mountTime({atMs:third.atMs, rawTexts, stepId:third.id}), "7.001");
  assert.ok(Number.isNaN(state.draft.steps[0].atMs));
  assert.match(paths.validationError(state.draft,catalog), /最多保留 3 位小数/);
});

test("opening another plan or reloading it clears cached raw times even when step identities are reused", () => {
  const original = plan("first-plan",0), replacement = plan("other-plan",1);
  const rawTexts = new Map([[original.steps[0].id,"1.2345"]]);
  const context = {stepTimeTexts:{current:rawTexts},shareAttempt:{current:0},setShareText:()=>{},setShareHelp:()=>{},
    setSelected:()=>{},setDraft:()=>{},setSavedDraft:()=>{},setPreviewStep:()=>{},setCharacterSearch:()=>{},
    setConflict:()=>{},setError:()=>{},setValidationFocus:()=>{},setShowAllIssues:()=>{}};
  for (const next of [replacement,replacement]) {
    rawTexts.set(original.steps[0].id,"1.2345");
    paths.reopenDraft(context,next);
    assert.equal(rawTexts.size,0);
    assert.equal(paths.mountTime({atMs:next.steps[0].atMs,stepId:next.steps[0].id,rawTexts}),"1");
  }
});

test("problem selection follows an exact moved step and refuses deleted or ambiguous targets", () => {
  const {state} = stepFixture();
  const issue = {code: "step_time", field: "stepTime", message: "unrelated display text", stepId: "existing-b", stepIndex: 1};
  const selected = [], focuses = [];
  const draft = {...state.draft, steps: [state.draft.steps[1], state.draft.steps[0], state.draft.steps[2]]};
  const before = structuredClone(draft);
  const context = {draft, setPreviewStep: (value) => selected.push(value), setValidationFocus: (value) => focuses.push(value)};
  paths.locate(context, issue);
  assert.deepEqual(selected, ["existing-b"]); assert.equal(focuses[0].stepIndex, 0);
  assert.deepEqual(draft, before);
  paths.locate({...context, draft: {...draft, steps: draft.steps.filter((step) => step.id !== issue.stepId)}}, issue);
  paths.locate({...context, draft: {...draft, steps: [draft.steps[0], draft.steps[1], {...draft.steps[0]}]}}, issue);
  assert.equal(selected.length, 1);
  paths.locate(context, {code: "name_required", field: "name", message: "第 99 步 should not be parsed"});
  assert.equal(selected.length, 1); assert.equal(focuses.at(-1).field, "name");
});

test("the actual focus effect uses structured field selectors and exact identity indexes", () => {
  const {state} = stepFixture();
  const events = [];
  const target = {scrollIntoView: (value) => events.push(["scroll", value]), focus: (value) => events.push(["focus", value])};
  const workshop = {current: {querySelector: (value) => {events.push(["query", value]); return target;}}};
  const context = {draft: state.draft, currentStep: state.draft.steps[1], workshop, setValidationFocus: (value) => events.push(["clear", value])};
  paths.performFocus({...context, validationFocus: {field: "stepAction", stepId: "existing-b", stepIndex: 1}});
  assert.equal(events[0][1], '[data-rotation-field="stepAction"]');
  assert.deepEqual(events.map(([type]) => type), ["query", "scroll", "focus", "clear"]);
  events.length = 0;
  paths.performFocus({...context, validationFocus: {field: "stepIdentity", stepId: "existing-b", stepIndex: 1}});
  assert.equal(events[0][1], '[data-step-index="1"] .rotation-step-select');
  events.length = 0;
  paths.performFocus({...context, validationFocus: {field: "stepTime", stepId: "deleted", stepIndex: 1}});
  assert.deepEqual(events, [["clear", null]]);
});

test("duplicate identities cannot focus the first matching step even when the original index still matches", () => {
  const {state} = stepFixture();
  const draft = {...state.draft, steps: [state.draft.steps[0], {...state.draft.steps[1], id: state.draft.steps[0].id}]};
  const selections = [], focuses = [];
  const context = {draft, setPreviewStep: (value) => selections.push(value), setValidationFocus: (value) => focuses.push(value)};
  for (const stepIndex of [0, 1]) for (const field of ["stepTime", "stepCharacter", "stepAction", "stepNote", "stepPhase"]) {
    paths.locate(context, {field, stepId: draft.steps[stepIndex].id, stepIndex, message: "display only"});
  }
  assert.deepEqual(selections, []); assert.deepEqual(focuses, []);
  paths.locate(context, {field: "stepIdentity", stepId: draft.steps[1].id, stepIndex: 1, message: "duplicate identity"});
  assert.deepEqual(selections, [draft.steps[1].id]); assert.equal(focuses[0].stepIndex, 1);
  const selectors = [];
  paths.performFocus({draft, currentStep: draft.steps[0], validationFocus: focuses[0], setValidationFocus: () => {},
    workshop: {current: {querySelector: (selector) => {selectors.push(selector); return {scrollIntoView() {}, focus() {}};}}}});
  assert.deepEqual(selectors, ['[data-step-index="1"] .rotation-step-select']);
});

function shareFixture({clipboard, busy = false, unauthorized = false} = {}) {
  const fixture = stepFixture();
  const state = {text: "", help: "", error: "", focused: [], writes: []};
  const draft = fixture.state.draft;
  const context = {busy, unauthorized, catalog: fixture.catalog, draft, accountId: "owner-a", shareAttempt: {current: 0},
    shareContext: {current: {draft, accountId: "owner-a", unauthorized}},
    navigator: clipboard === null ? {} : {clipboard: {writeText: clipboard || (async (text) => {state.writes.push(text);})}},
    setError: (value) => {state.error = value;}, focusIssue: (value) => state.focused.push(value),
    setShareText: (value) => {state.text = value;}, setShareHelp: (value) => {state.help = value;}};
  return {state, context};
}

test("sharing writes exactly the existing v2 export without changing exact roots, same-time order or the draft", async () => {
  const {state, context} = shareFixture();
  const before = structuredClone(context.draft);
  await paths.share(context);
  assert.deepEqual(state.writes, [paths.serializeRotationExport(context.draft)]);
  const exported = JSON.parse(state.writes[0]);
  assert.equal(exported.format, "zmdlogs.rotation.v2"); assert.equal(exported.schemaVersion, 2);
  assert.deepEqual(exported.rotation.steps.map((step) => step.id), before.steps.map((step) => step.id));
  assert.equal(exported.rotation.steps[0].skillId, "root-a");
  assert.equal(Object.hasOwn(exported.rotation.steps[0], "actionKind"), false);
  assert.equal(exported.rotation.steps[1].actionKind, "battle_skill");
  assert.deepEqual(context.draft, before); assert.equal(state.text, ""); assert.match(state.help, /已复制/);
});

test("clipboard denial and missing API expose only an explicit manual-copy fallback", async () => {
  for (const clipboard of [null, async () => {throw new Error("denied");}]) {
    const {state, context} = shareFixture({clipboard});
    await paths.share(context);
    assert.equal(state.text, paths.serializeRotationExport(context.draft));
    assert.match(state.help, /手动复制/); assert.doesNotMatch(state.help, /已复制/);
  }
});

test("invalid drafts never reach clipboard or JSON fallback, and focus their structured issue", async () => {
  for (const atMs of [Number.NaN, Infinity, -1, 0.5]) {
    const {state, context} = shareFixture();
    context.draft = {...context.draft, steps: [{...context.draft.steps[0], atMs}]};
    await paths.share(context);
    assert.deepEqual(state.writes, []); assert.equal(state.text, ""); assert.equal(state.help, "");
    assert.equal(state.focused[0].field, "stepTime"); assert.match(state.error, /时间/);
  }
  for (const options of [{busy:true}, {unauthorized:true}]) {
    const {state, context} = shareFixture(options); await paths.share(context);
    assert.deepEqual(state.writes, []); assert.equal(state.help, "");
  }
});

test("pending clipboard success or denial cannot show stale feedback after draft, account, or auth changes", async () => {
  for (const result of ["success", "denied"]) for (const change of ["draft", "account", "unauthorized", "openedDraft"]) {
    let finish;
    const pending = new Promise((resolve, reject) => {finish = result === "success" ? resolve : () => reject(new Error("denied"));});
    const {state, context} = shareFixture({clipboard: () => pending});
    const operation = paths.share(context);
    if (change === "draft") context.shareContext.current = {...context.shareContext.current, draft: {...context.draft, name: "new draft"}};
    if (change === "account") context.shareContext.current = {...context.shareContext.current, accountId: "owner-b"};
    if (change === "unauthorized") context.shareContext.current = {...context.shareContext.current, unauthorized: true};
    if (change === "openedDraft") context.shareAttempt.current += 1;
    finish(); await operation;
    assert.equal(state.help, "", `${result}/${change}`); assert.equal(state.text, "", `${result}/${change}`);
  }
});

test("the actual share effect clears fallback on editing and invalidates pending responses at unmount", async () => {
  let finish;
  const pending = new Promise((resolve) => {finish = resolve;});
  const {state, context} = shareFixture({clipboard: () => pending});
  state.text = "old contents"; state.help = "old feedback";
  const cleanup = paths.shareLifecycle(context);
  assert.equal(state.text, ""); assert.equal(state.help, "");
  const operation = paths.share(context);
  cleanup(); finish(); await operation;
  assert.equal(state.text, ""); assert.equal(state.help, "");
});

test("a metadata-only own reorder advances the saved revision and preserves dirty content", async () => {
  const { context, state } = reorderContext();
  const before = JSON.stringify(state.draft);
  await paths.reorder(context, 0, 1);
  assert.equal(state.selected.revision, 5);
  assert.equal(JSON.stringify(state.draft), before);
  assert.equal(state.openCalls, 0);
  assert.equal(state.conflict, false);
  assert.equal(state.busy, false);
  assert.deepEqual(state.requests[0], { path: "/order", body: { ids: ["axis-b", "axis-a"] }, owner: "account-a" });
});

test("a concurrent content change cannot lend a newer revision to an old dirty draft", async () => {
  const { context, state } = reorderContext({ remoteNote: "edited in another tab" });
  const before = JSON.stringify(state.draft);
  await paths.reorder(context, 0, 1);
  assert.equal(state.selected.revision, 4);
  assert.equal(JSON.stringify(state.draft), before);
  assert.equal(state.openCalls, 0);
  assert.equal(state.conflict, true);
  assert.match(state.error, /草稿已保留/);
});

test("a clean editor adopts the full reordered plan and out-of-bounds moves do nothing", async () => {
  const { context, state } = reorderContext({ dirty: false });
  await paths.reorder(context, 0, -1);
  assert.equal(state.requests.length, 0);
  await paths.reorder(context, 0, 1);
  assert.equal(state.selected.revision, 5);
  assert.equal(state.openCalls, 1);
});

test("copies reserve the suffix inside the 80-unit name limit without splitting Unicode characters", () => {
  for (const originalName of ["长".repeat(80), "😀".repeat(40), ""]) {
    let copied;
    const original = body(plan("original", 0)); original.name = originalName;
    paths.copy({ catalog: {}, draft: original, setNotice: () => {}, setError: () => {},
      openDraft: (selected, draft) => { assert.equal(selected, null); copied = draft; }, crypto: { randomUUID: () => "new-step-id" } });
    assert.ok(copied.name.length <= 80);
    assert.ok(copied.name.endsWith(" · 副本"));
    assert.equal(copied.name.isWellFormed(), true);
    assert.equal(copied.enabled, false);
    assert.equal(copied.steps[0].id, "new-step-id");
    assert.equal(original.steps[0].id, "step-1");
  }
});

test("unauthorized new/import handlers preserve the existing recovery state", () => {
  // No editor-state or parser mocks are supplied: reaching them would throw.
  assert.doesNotThrow(() => paths.unauthorizedNew());
  assert.doesNotThrow(() => paths.unauthorizedImport());
});

const buttonDisabled = (label) => nodeMatching((node) => ts.isJsxElement(node) && node.openingElement.tagName.getText(tree) === "button"
  && node.children.some((child) => ts.isJsxText(child) && child.getText(tree).trim() === label)).openingElement.attributes.properties
  .find((attribute) => ts.isJsxAttribute(attribute) && attribute.name.getText(tree) === "disabled").initializer.expression.getText(tree);
const { libraryDisabled } = await import(moduleUrl(`export function libraryDisabled({busy, catalog, unauthorized}) {
  return {newPlan: ${buttonDisabled("＋ 新建")}, import: ${buttonDisabled("导入")}};
}`));
test("the library buttons cannot dismiss recovery by opening new/import while unauthorized", () => {
  assert.deepEqual(libraryDisabled({ busy: false, catalog: {}, unauthorized: true }), { newPlan: true, import: true });
  assert.deepEqual(libraryDisabled({ busy: false, catalog: {}, unauthorized: false }), { newPlan: false, import: false });
});

const effect = nodeMatching((node) => ts.isCallExpression(node) && node.expression.getText(tree) === "useEffect"
  && node.arguments[0]?.getText(tree).includes('"beforeunload"')).arguments[0].getText(tree);
const { mountGuard } = await import(moduleUrl(`
  export function mountGuard({ dirty, window, document, Element, HTMLAnchorElement }) { return (${effect})(); }
`));
class ClickElement {
  constructor(link) { this.link = link; }
  closest(selector) { assert.equal(selector, "a[href]"); return this.link; }
}
class ClickAnchor extends ClickElement {
  constructor(href, { target = "", download = false } = {}) { super(null); this.href = href; this.target = target; this.download = download; }
  hasAttribute(name) { return name === "download" && this.download; }
}
function navigationFixture({ dirty = true, allow = false } = {}) {
  const listeners = new Map(); const windows = new Map();
  const state = { prompts: 0, allowedRoutes: 0 };
  const cleanup = mountGuard({ dirty, Element: ClickElement, HTMLAnchorElement: ClickAnchor,
    window: { location: { href: "http://127.0.0.1:3314/rotations" }, confirm: () => { state.prompts += 1; return allow; },
      addEventListener: (name, handler) => windows.set(name, handler), removeEventListener: (name) => windows.delete(name) },
    document: { addEventListener: (name, handler, capture) => { assert.equal(capture, true); listeners.set(name, handler); },
      removeEventListener: (name, handler, capture) => { assert.equal(capture, true); listeners.delete(name); } },
  });
  const click = (href, anchorOptions = {}, eventOptions = {}) => {
    const event = { target: new ClickElement(new ClickAnchor(href, anchorOptions)), defaultPrevented: false, stopped: false,
      button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...eventOptions,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; } };
    listeners.get("click")?.(event);
    if (!event.defaultPrevented && !event.stopped) state.allowedRoutes += 1;
    return event;
  };
  return { state, listeners, windows, cleanup, click };
}

test("declining a dirty ordinary same-origin link cancels the native capture event", () => {
  const fixture = navigationFixture();
  const event = fixture.click("http://127.0.0.1:3314/");
  assert.equal(fixture.state.prompts, 1);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.stopped, true);
  assert.equal(fixture.state.allowedRoutes, 0);
  fixture.cleanup();
  assert.equal(fixture.listeners.size, 0); assert.equal(fixture.windows.size, 0);
});

test("accepting the dirty link leaves the native capture event uncancelled", () => {
  const fixture = navigationFixture({ allow: true });
  const event = fixture.click("http://127.0.0.1:3314/character/chr_0015_lifeng");
  assert.equal(fixture.state.prompts, 1);
  assert.equal(event.defaultPrevented, false);
  assert.equal(event.stopped, false);
  assert.equal(fixture.state.allowedRoutes, 1);
  fixture.cleanup();
});

test("hash, new-window, download and modified clicks do not prompt or block", () => {
  const fixture = navigationFixture();
  const examples = [
    ["http://127.0.0.1:3314/rotations#preview", {}, {}],
    ["http://127.0.0.1:3314/", { target: "_blank" }, {}],
    ["http://127.0.0.1:3314/downloads/client.zip", { download: true }, {}],
    ["http://127.0.0.1:3314/", {}, { ctrlKey: true }],
    ["http://127.0.0.1:3314/", {}, { metaKey: true }],
    ["http://127.0.0.1:3314/", {}, { shiftKey: true }],
    ["http://127.0.0.1:3314/", {}, { altKey: true }],
    ["http://127.0.0.1:3314/", {}, { button: 1 }],
    ["https://example.com/", {}, {}],
  ];
  for (const example of examples) {
    const event = fixture.click(...example);
    assert.equal(event.defaultPrevented, false); assert.equal(event.stopped, false);
  }
  assert.equal(fixture.state.prompts, 0);
  fixture.cleanup();
});

test("a clean editor installs no leave protection", () => {
  const fixture = navigationFixture({ dirty: false });
  assert.equal(fixture.listeners.size, 0); assert.equal(fixture.windows.size, 0); assert.equal(fixture.cleanup, undefined);
  fixture.click("http://127.0.0.1:3314/");
  assert.equal(fixture.state.prompts, 0);
});
