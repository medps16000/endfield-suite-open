import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

async function sourceFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".next")) continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await sourceFiles(path));
    else if (/\.(?:ts|tsx)$/.test(entry.name)) files.push(path);
  }
  return files;
}

test("public web modules do not require undistributed game JSON at import time", async () => {
  const forbidden = /(?:from\s*["']|import\s*\(\s*["']|require\s*\(\s*["'])[^"']*(?:\/data\/|\/public\/(?:images|endaxis)\/)[^"']*\.json["']/;
  for (const folder of ["app", "components", "features", "lib"]) {
    for (const file of await sourceFiles(join(root, folder))) {
      assert.doesNotMatch(await readFile(file, "utf8"), forbidden, file);
    }
  }
});

test("public resource defaults are explicitly unavailable and keep the Endaxis stub", async () => {
  const resources = await readFile(join(root, "lib/generated/runtime-resources.ts"), "utf8");
  assert.match(resources, /Empty collections mean resources are unavailable/);
  assert.doesNotMatch(resources, /\/images\//);
  assert.match(await readFile(join(root, "lib/endaxis-project.ts"), "utf8"), /from "\.\/generated\/endaxis-gamedata"/);
  assert.match(await readFile(join(root, "lib/generated/endaxis-gamedata.ts"), "utf8"), /characterRoster:\s*\[\]/);
});
