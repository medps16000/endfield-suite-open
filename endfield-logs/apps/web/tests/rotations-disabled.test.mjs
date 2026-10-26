import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const read = (path) => readFile(new URL(path, import.meta.url), "utf8");
const Link = ({ href, children, ...props }) => React.createElement("a", { ...props, href }, children);
function load(source, dependencies = {}) {
  const compiled = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled)((name) => {
    if (name.endsWith(".css")) return {};
    if (name === "next/link") return { default: Link };
    if (name in dependencies) return dependencies[name];
    return require(name);
  }, module, module.exports);
  return module.exports;
}

const flags = load(await read("../lib/feature-flags.ts"));
const state = load(await read("../components/state-block.tsx"));
const unavailable = load(await read("../components/rotations-unavailable.tsx"), { "./state-block": state });
const pausedText = "排轴功能暂时停用";

test("one source flag disables rotations by default and the notice retains a way home", () => {
  assert.equal(flags.ROTATIONS_ENABLED, false);
  const html = renderToStaticMarkup(React.createElement(unavailable.RotationsUnavailable));
  assert.ok(html.includes(pausedText));
  assert.ok(html.includes("已保存的排轴数据仍会保留"));
  assert.ok(html.includes('href="/"'));
  assert.equal(/<form|<input|<button/.test(html), false);
});

test("all dedicated rotation pages stop before authentication, battle reads or editor mounting", async () => {
  const never = () => { throw new Error("disabled rotation page accessed active dependencies"); };
  for (const [path, prefix] of [
    ["../app/rotations/page.tsx", "../../"],
    ["../app/axis/[battleId]/page.tsx", "../../../"],
    ["../app/axis/[battleId]/editor/page.tsx", "../../../../"],
  ]) {
    const source = await read(path);
    const page = load(source, {
      [`${prefix}lib/feature-flags`]: flags,
      [`${prefix}components/rotations-unavailable`]: unavailable,
      [`${prefix}components/state-block`]: state,
      [`${prefix}lib/api/server`]: { getCurrentUser: never, fetchApiServer: never },
      [`${prefix}components/rotation-editor`]: { RotationEditor: never },
      [`${prefix}features/battle-detail/battle-detail-view`]: { BattleDetailView: never },
      [`${prefix}lib/endaxis-project`]: { buildEndaxisImportPayloadFromExport: never },
      "./endaxis-preload": { EndaxisPreload: never },
    });
    const html = renderToStaticMarkup(await page.default({}));
    assert.ok(html.includes(pausedText), path);
    assert.equal(/登录后开始编排|正在打开排轴|ROTATION WORKSHOP/.test(html), false, path);
  }
});

test("site navigation hides the rotation link and one flag can restore the original entry", async () => {
  const source = await read("../components/site-nav-links.tsx");
  function render(enabled) {
    const { SiteNavLinks } = load(source, {
      "next/navigation": { usePathname: () => "/" }, "./about-dialog": { AboutDialog: () => null },
      "../features/records/ranking-groups.generated": { rankingGroups: [] },
      "../lib/feature-flags": { ROTATIONS_ENABLED: enabled },
    });
    return renderToStaticMarkup(React.createElement(SiteNavLinks));
  }
  assert.equal(render(false).includes('href="/rotations"'), false);
  assert.equal(render(true).includes('href="/rotations"'), true);
});

test("battle and personal-record rotation links use the same flag without hiding battle details", async () => {
  for (const path of ["../features/battle-detail/battle-detail-view.tsx", "../features/records/user-battles-dashboard.tsx"]) {
    const source = await read(path);
    const tree = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const expressions = new Set();
    function visit(node) {
      if (ts.isJsxElement(node) && node.openingElement.tagName.getText(tree) === "Link"
          && node.openingElement.attributes.getText(tree).includes("/axis/")) {
        let parent = node.parent;
        while (parent && !ts.isJsxExpression(parent)) parent = parent.parent;
        assert.ok(parent && parent.getText(tree).includes("ROTATIONS_ENABLED"), path);
        expressions.add(parent.expression.getText(tree));
      }
      ts.forEachChild(node, visit);
    }
    visit(tree);
    assert.ok(expressions.size > 0, path);
    const body = [...expressions].map((expression) => `{${expression}}`).join("");
    function render(enabled) {
      const { Links } = load(`import Link from "next/link";
        export function Links() { const ROTATIONS_ENABLED = ${enabled};
          const axisOnly = false, detail = { battle: { id: "battle-1" } }, battle = detail.battle;
          return <>${body}</>; }`);
      return renderToStaticMarkup(React.createElement(Links));
    }
    assert.equal(render(false).includes("/axis/"), false, path);
    assert.equal(render(true).includes("/axis/"), true, path);
    assert.ok(source.includes("/battle/"), "ordinary battle navigation remains available");
  }
});

test("restoring the flag retains the account rotation editor path", async () => {
  const source = await read("../app/rotations/page.tsx");
  const { default: Page } = load(source, {
    "../../lib/feature-flags": { ROTATIONS_ENABLED: true },
    "../../components/rotations-unavailable": unavailable,
    "../../lib/api/server": { getCurrentUser: async () => ({ id: "account-1" }) },
    "../../components/rotation-editor": { RotationEditor: ({ accountId }) => React.createElement("div", null, `editor:${accountId}`) },
  });
  const html = renderToStaticMarkup(await Page());
  assert.ok(html.includes("editor:account-1"));
  assert.equal(html.includes(pausedText), false);
});

test("the local rotation proxy refuses every method before reading input or reaching an upstream", async () => {
  const source = await read("../app/api/rotations/[[...path]]/route.ts");
  const proxy = load(source, { "../../../../lib/feature-flags": flags });
  const previousFetch = globalThis.fetch;
  const never = () => { throw new Error("disabled proxy must not access input or upstream"); };
  globalThis.fetch = never;
  try {
    for (const method of ["GET", "POST", "PATCH", "DELETE"]) {
      const request = new Request("http://127.0.0.1/api/rotations", { method });
      Object.defineProperty(request, "text", { value: never });
      const context = { get params() { return never(); } };
      const response = await proxy[method](request, context);
      assert.equal(response.status, 410, method);
      assert.equal(response.headers.get("Cache-Control"), "no-store", method);
      assert.deepEqual(await response.json(), { error: { code: "ROTATIONS_DISABLED", message: "排轴功能暂时停用。" } });
    }
  } finally {
    globalThis.fetch = previousFetch;
  }
});
