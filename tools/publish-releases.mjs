// Uploads the map files built in this run to their GitHub releases.
// Region files go to maps-latest. Square files go to the release named in their index entry.
// Checks each release's asset count (GitHub allows 1,000 per release) before uploading anything.
// Usage: node tools/publish-releases.mjs [--dry-run]
// Needs the gh CLI and GH_TOKEN when not a dry run. Exits 1 if there is nothing to upload.

import { readFile, readdir } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PUBLISHED = join(ROOT, "data", "published");
const SQUARES = join(PUBLISHED, "squares");
const MAX_ASSETS_PER_RELEASE = 1000;
const CHUNK = 50;
const dryRun = process.argv.includes("--dry-run");

const exists = (p) => readFile(p).then(() => true, () => false);
const list = async (dir) => (await readdir(dir).catch(() => [])).map((f) => join(dir, f));

// Map from release tag to the local files to upload to it.
const plan = new Map();
const add = (tag, file) => {
  if (!plan.has(tag)) plan.set(tag, []);
  plan.get(tag).push(file);
};

// Region files: one asset each, all in maps-latest.
for (const f of await list(PUBLISHED)) if (f.endsWith(".dmap")) add("maps-latest", f);

// Square files: the .dmap and the .dmap.gz, in the release recorded in the index.
const squareIndex = JSON.parse(await readFile(join(SQUARES, "index.json"), "utf8"));
for (const f of await list(SQUARES)) {
  if (!f.endsWith(".dmap") && !f.endsWith(".dmap.gz")) continue;
  const id = f.replace(/\.dmap(\.gz)?$/, "").split("/").pop();
  const entry = squareIndex[id];
  if (!entry || entry.empty) throw new Error(`${f}: no built square entry in the index`);
  add(entry.release ?? "maps-latest", f);
}

if (plan.size === 0) {
  console.error("No map files found to upload.");
  process.exit(1);
}

// Refuse before uploading anything if a release would get too many assets.
for (const [tag, files] of plan) {
  const existing = dryRun ? 0 : countRemoteAssets(tag);
  if (existing + files.length > MAX_ASSETS_PER_RELEASE) {
    console.error(`${tag}: ${existing} assets there plus ${files.length} new is over ${MAX_ASSETS_PER_RELEASE}. Nothing uploaded.`);
    process.exit(1);
  }
}

function countRemoteAssets(tag) {
  const r = spawnSync("gh", ["release", "view", tag, "--json", "assets", "--jq", ".assets | length"], { encoding: "utf8" });
  if (r.status !== 0) return 0; // release does not exist yet
  return Number(r.stdout.trim()) || 0;
}

function gh(args) {
  const r = spawnSync("gh", args, { stdio: "inherit" });
  if (r.status !== 0) throw new Error(`gh ${args.join(" ")} failed with ${r.status}`);
}

for (const [tag, files] of plan) {
  console.log(`${tag}: ${files.length} file(s)${dryRun ? " (dry run)" : ""}`);
  if (dryRun) continue;
  const have = spawnSync("gh", ["release", "view", tag], { stdio: "ignore" }).status === 0;
  if (!have) {
    gh(["release", "create", tag, "--title", `Drivemate map files (${tag})`, "--notes", "Automatically updated map files. Each file replaces the previous version.", "--latest=false"]);
  }
  for (let i = 0; i < files.length; i += CHUNK) {
    gh(["release", "upload", tag, ...files.slice(i, i + CHUNK), "--clobber"]);
  }
}
console.log(`${[...plan.values()].reduce((n, f) => n + f.length, 0)} file(s) in ${plan.size} release(s)`);
