import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const compile = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const url = (source) => `data:text/javascript;base64,${Buffer.from(compile(source)).toString("base64")}`;
const client = await readFile(new URL("../lib/api/client.ts", import.meta.url), "utf8");
const rotations = await readFile(new URL("../lib/api/rotations.ts", import.meta.url), "utf8");
const { parseRotationImport, ROTATION_IMPORT_MAX_BYTES, rotationRequest, RotationApiError, serializeRotationExport } = await import(url(rotations.replace('"./client"', JSON.stringify(url(client)))));
const proxySource = await readFile(new URL("../app/api/rotations/[[...path]]/route.ts", import.meta.url), "utf8");
// Keep the existing upstream contract covered when the feature is restored.
const enabledFlags = url("export const ROTATIONS_ENABLED = true;");
const proxy = await import(url(proxySource.replace('"../../../../lib/feature-flags"', JSON.stringify(enabledFlags))));

const plan = () => ({ name: "双技能轴", dungeonId: "indie_hard026_s", enabled: true,
  characterIds: ["chr_0034_typhoea", "chr_0035_liino", "chr_0015_lifeng", "chr_0025_ardelia"], displayNotes: "武器精炼仅展示",
  steps: [{ id: "first", atMs: 5000, characterId: "chr_0034_typhoea", skillId: "chr_0034_typhoea_normal_skill", note: "切前台", phase: "开场" }] });
const file = (rotation = plan()) => JSON.stringify({ format: "zmdlogs.rotation.v1", schemaVersion: 1, rotation });

test("imports make a private draft without server ownership, revision or activation", () => {
  const draft = parseRotationImport(file({ ...plan(), id: "someone-elses-plan", revision: 999, accountId: "another-account", position: -1, updatedAt: "yesterday" }));
  assert.equal(draft.enabled, false);
  assert.equal(draft.steps[0].atMs, 5000);
  for (const key of ["id", "accountId", "position", "revision", "updatedAt"]) assert.equal(key in draft, false);
});

test("unknown versions and malformed JSON cannot be imported", () => {
  for (const text of ["not-json", "null", '[]', JSON.stringify({ format: "EHR1", schemaVersion: 1, rotation: plan() }),
    JSON.stringify({ format: "zmdlogs.rotation.v1", schemaVersion: 2, rotation: plan() })]) assert.throws(() => parseRotationImport(text));
});

test("action exports round trip as version 2 while old exact steps retain their meaning", () => {
  const draft = plan();
  draft.steps.push({ id: "second", atMs: 6000, characterId: draft.characterIds[0], actionKind: "ultimate_skill", note: "开大招", phase: "开场" });
  const exported = serializeRotationExport(draft);
  assert.equal(JSON.parse(exported).schemaVersion, 2);
  const imported = parseRotationImport(exported);
  assert.deepEqual(imported, { ...draft, enabled: false });
  assert.equal("actionKind" in imported.steps[0], false);
  assert.equal("skillId" in imported.steps[1], false);
  const old = parseRotationImport(file());
  assert.equal(old.steps[0].skillId, plan().steps[0].skillId);
  assert.equal("actionKind" in old.steps[0], false);
});

test("imports reject ambiguous, empty, unknown actions and action fields in old files", () => {
  const step = plan().steps[0];
  const fileV2 = (step) => JSON.stringify({ format: "zmdlogs.rotation.v2", schemaVersion: 2, rotation: { ...plan(), steps: [step] } });
  const {skillId, ...withoutSkill} = step;
  for (const invalid of [{...step, actionKind: "battle_skill"}, {...withoutSkill}, {...withoutSkill, actionKind: ""},
    {...withoutSkill, actionKind: "normal_attack"}, {...withoutSkill, actionKind: null}, {...withoutSkill, skillId: null, actionKind: "battle_skill"},
    {...withoutSkill, skillId: ""}, {...withoutSkill, skillId: "", actionKind: "battle_skill"}]) {
    assert.throws(() => parseRotationImport(fileV2(invalid)));
  }
  assert.throws(() => parseRotationImport(file({ ...plan(), steps: [{ ...withoutSkill, actionKind: "battle_skill" }] })));
  for (const actionKind of ["battle_skill", "combo_skill", "ultimate_skill"]) {
    assert.equal(parseRotationImport(fileV2({...withoutSkill, actionKind})).steps[0].actionKind, actionKind);
  }
});

test("imports enforce four unique characters, bounded payload, steps and exact integer milliseconds", () => {
  assert.throws(() => parseRotationImport(" ".repeat(ROTATION_IMPORT_MAX_BYTES + 1)));
  assert.throws(() => parseRotationImport("汉".repeat(Math.floor(ROTATION_IMPORT_MAX_BYTES / 3) + 1)), /8 MiB/);
  for (const characters of [[], ["a", "b", "c"], ["a", "b", "c", "a"]]) assert.throws(() => parseRotationImport(file({ ...plan(), characterIds: characters })));
  for (const atMs of [-1, 3_600_001, 1.5, "5000"]) assert.throws(() => parseRotationImport(file({ ...plan(), steps: [{ ...plan().steps[0], atMs }] })));
  assert.throws(() => parseRotationImport(file({ ...plan(), steps: [] })));
  assert.throws(() => parseRotationImport(file({ ...plan(), steps: [{ ...plan().steps[0], id: " \n\t " }] })));
  assert.throws(() => parseRotationImport(file({ ...plan(), steps: Array.from({ length: 501 }, (_, i) => ({ ...plan().steps[0], id: String(i) })) })));
});

test("a maximal legal 500-step plan with official identities can import its own pretty export", async () => {
  const characters = JSON.parse(await readFile(new URL("../../../data/local_tables/character/manifest.json", import.meta.url), "utf8")).entries;
  const avatars = JSON.parse(await readFile(new URL("../public/images/character/game-avatars/manifest.json", import.meta.url), "utf8")).avatars;
  const officialSkills = JSON.parse(await readFile(new URL("../../../data/local_static/skill/display_name_catalog.json", import.meta.url), "utf8"));
  const officialMap = JSON.parse(await readFile(new URL("../../../data/local_tables/dungeon/items/indie_group_h07.json", import.meta.url), "utf8")).childDungeons.indie_hard026_s;
  assert.equal(officialMap.dungeonId, "indie_hard026_s");
  const squad = characters.filter((character) => character.id in avatars).slice(0, 4).map((character) => character.id);
  assert.equal(new Set(squad).size, 4);
  const root = Object.entries(officialSkills.entries).find(([id, entry]) => !(id in officialSkills.conflicts)
    && entry.proofKind === "official_char_growth_skill_list"
    && entry.paths.some((path) => path.characterId === squad[0] && path.rootSkillId === id && path.steps.length === 0 && entry.skillGroupIds.includes(path.skillGroupId)));
  assert.ok(root, "fixture skill must have an exact official direct-root relation");
  const draft = { name: "极限往返测试", dungeonId: "indie_hard026_s", characterIds: squad, enabled: true, displayNotes: "配装".repeat(1000),
    steps: Array.from({ length: 500 }, (_, index) => ({ id: `${"轴".repeat(77)}${String(index).padStart(3, "0")}`, atMs: index * 1000,
      characterId: squad[0], skillId: root[0], note: "备".repeat(499), phase: "段".repeat(80) })) };
  const exported = serializeRotationExport(draft);
  const bytes = new TextEncoder().encode(exported).length;
  assert.ok(bytes > 1024 * 1024, "the regression fixture must exceed the old 1 MiB import limit");
  assert.ok(bytes < ROTATION_IMPORT_MAX_BYTES);
  const imported = parseRotationImport(exported);
  assert.deepEqual(imported, { ...draft, enabled: false });
});

test("duplicate step identities and backwards timing are rejected, simultaneous skills remain ordered", () => {
  const first = plan().steps[0];
  assert.throws(() => parseRotationImport(file({ ...plan(), steps: [first, first] })));
  assert.throws(() => parseRotationImport(file({ ...plan(), steps: [first, { ...first, id: "second", atMs: 4999 }] })));
  const simultaneous = parseRotationImport(file({ ...plan(), steps: [first, { ...first, id: "second" }] }));
  assert.deepEqual(simultaneous.steps.map((step) => step.id), ["first", "second"]);
});

test("private requests use existing cookies and preserve conflict errors", async () => {
  const originalFetch = globalThis.fetch;
  let observed;
  globalThis.fetch = async (path, init) => { observed = { path, init }; return Response.json({ error: { message: "排轴已被修改，请重新载入。" } }, { status: 409 }); };
  try {
    await assert.rejects(rotationRequest("/plan-1", { method: "PATCH", body: JSON.stringify({ revision: 7 }) }, "expected-account"),
      (error) => error instanceof RotationApiError && error.status === 409 && error.message.includes("重新载入"));
    assert.ok(observed.path.endsWith("/api/rotations/plan-1"));
    assert.equal(observed.init.credentials, "include");
    assert.equal(observed.init.cache, "no-store");
    assert.equal(observed.init.headers.get("content-type"), "application/json");
    assert.equal(observed.init.headers.get("x-endfield-rotation-account"), "expected-account");
  } finally { globalThis.fetch = originalFetch; }
});

test("rotation browser requests stay on the scoped same-origin route even with a separate public API base", async () => {
  const externalClient = client.replace("process.env.NEXT_PUBLIC_API_BASE_URL", JSON.stringify("https://separate-api.invalid"));
  const isolated = await import(url(rotations.replace('"./client"', JSON.stringify(url(externalClient)))));
  const originalFetch = globalThis.fetch;
  const observed = [];
  const controller = new AbortController();
  globalThis.fetch = async (path, init) => { observed.push({ path, init }); return Response.json({}); };
  try {
    await isolated.rotationRequest("/catalog", { signal: controller.signal });
    await isolated.rotationRequest("", { signal: controller.signal }, "expected-account");
    assert.deepEqual(observed.map(({ path }) => path), ["/api/rotations/catalog", "/api/rotations"]);
    for (const { init } of observed) {
      assert.equal(init.signal, controller.signal);
      assert.equal(init.credentials, "include");
      assert.equal(init.cache, "no-store");
    }
    assert.equal(observed[1].init.headers.get("x-endfield-rotation-account"), "expected-account");
  } finally { globalThis.fetch = originalFetch; }
});

test("account changes retain their distinct code instead of becoming ordinary revision conflicts", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({ error: { code: "ROTATION_ACCOUNT_CHANGED", message: "登录账号已变化。" } }, { status: 409 });
  try {
    await assert.rejects(rotationRequest("", undefined, "original-account"),
      (error) => error instanceof RotationApiError && error.status === 409 && error.code === "ROTATION_ACCOUNT_CHANGED");
  } finally { globalThis.fetch = originalFetch; }
});

test("cancelled initial-load requests preserve cancellation instead of reporting a connection failure", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const cancellation = new DOMException("The operation was aborted.", "AbortError");
  let calls = 0;
  globalThis.fetch = async (path, init) => {
    calls += 1;
    assert.equal(init.signal, controller.signal);
    controller.abort();
    throw cancellation;
  };
  try {
    await assert.rejects(rotationRequest("", { signal: controller.signal }, "expected-account"), (error) => error === cancellation);
    assert.equal(calls, 1, "cancellation must not trigger a retry");
  } finally { globalThis.fetch = originalFetch; }
});

test("the scoped preview proxy forwards the expected account, cookies and origin without hiding account-change errors", async () => {
  const originalFetch = globalThis.fetch;
  let observed;
  globalThis.fetch = async (path, init) => {
    observed = { path, init };
    return Response.json({ error: { code: "ROTATION_ACCOUNT_CHANGED", message: "登录账号已变化。" } }, { status: 409 });
  };
  try {
    const request = new Request("http://127.0.0.1:3314/api/rotations/rot_test", { method: "PATCH",
      headers: { "X-Endfield-Rotation-Account": "original-account", cookie: "endfield_logs_session=test-cookie", origin: "http://127.0.0.1:3314", "Content-Type": "application/json" },
      body: JSON.stringify({ revision: 1 }),
    });
    const response = await proxy.PATCH(request, { params: Promise.resolve({ path: ["rot_test"] }) });
    assert.equal(observed.init.headers.get("x-endfield-rotation-account"), "original-account");
    assert.equal(observed.init.headers.get("cookie"), "endfield_logs_session=test-cookie");
    assert.equal(observed.init.headers.get("origin"), "http://127.0.0.1:3314");
    assert.equal(response.status, 409);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal((await response.json()).error.code, "ROTATION_ACCOUNT_CHANGED");
  } finally { globalThis.fetch = originalFetch; }
});

test("network and non-JSON successful responses give readable errors", async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new TypeError("network failed"); };
    await assert.rejects(rotationRequest(""), (error) => error.status === 0 && error.message.includes("检查网络"));
    globalThis.fetch = async () => new Response("<html>unavailable</html>");
    await assert.rejects(rotationRequest(""), (error) => error.status === 502 && error.message.includes("无法读取"));
  } finally { globalThis.fetch = originalFetch; }
});
