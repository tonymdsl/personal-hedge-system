import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const BASE_URL = "https://here.now";
const TARGET = path.resolve("published", "here-now");
const STATE_DIR = path.resolve(".herenow");
const STATE_FILE = path.join(STATE_DIR, "state.json");
const CLIENT = "codex/publish-js";

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon"
};

async function exists(file) {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}

async function readCredentials() {
  if (process.env.HERENOW_API_KEY) return { key: process.env.HERENOW_API_KEY.trim(), source: "env" };
  const credentialsPath = path.join(process.env.USERPROFILE || process.env.HOME || "", ".herenow", "credentials");
  if (credentialsPath && (await exists(credentialsPath))) {
    const key = (await fs.readFile(credentialsPath, "utf8")).trim();
    if (key) return { key, source: "credentials" };
  }
  return { key: "", source: "none" };
}

async function readState() {
  if (!(await exists(STATE_FILE))) return { publishes: {} };
  return JSON.parse(await fs.readFile(STATE_FILE, "utf8"));
}

async function walk(dir) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(full)));
    if (entry.isFile()) files.push(full);
  }
  return files;
}

async function sha256(file) {
  const bytes = await fs.readFile(file);
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function rel(file) {
  return path.relative(TARGET, file).replaceAll(path.sep, "/");
}

async function requestJson(url, init) {
  const response = await fetch(url, init);
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Non-JSON response from ${url}: ${text}`);
  }
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

const { key: apiKey, source: apiKeySource } = await readCredentials();
const state = await readState();
const firstSlug = Object.keys(state.publishes || {})[0] || "";
const claimToken = firstSlug ? state.publishes[firstSlug]?.claimToken || "" : "";
const authMode = apiKey ? "authenticated" : "anonymous";

const localFiles = await walk(TARGET);
if (!localFiles.length) throw new Error(`No files found in ${TARGET}`);

const files = await Promise.all(
  localFiles.map(async (file) => {
    const stat = await fs.stat(file);
    const ext = path.extname(file).toLowerCase();
    return {
      path: rel(file),
      size: stat.size,
      contentType: contentTypes[ext] || "application/octet-stream",
      hash: await sha256(file),
      localFile: file
    };
  })
);

const body = { files: files.map(({ localFile, ...item }) => item) };
if (!apiKey && firstSlug && claimToken) body.claimToken = claimToken;

const endpoint = firstSlug ? `${BASE_URL}/api/v1/publish/${firstSlug}` : `${BASE_URL}/api/v1/publish`;
const method = firstSlug ? "PUT" : "POST";
const headers = {
  "content-type": "application/json",
  "x-herenow-client": CLIENT
};
if (apiKey) headers.authorization = `Bearer ${apiKey}`;

console.error(`${firstSlug ? "updating" : "creating"} publish (${files.length} files)...`);
const create = await requestJson(endpoint, { method, headers, body: JSON.stringify(body) });
const uploads = create.upload?.uploads || [];

console.error(`uploading ${uploads.length} files...`);
for (const upload of uploads) {
  const file = files.find((item) => item.path === upload.path);
  if (!file) throw new Error(`Missing local file for upload path ${upload.path}`);
  const bytes = await fs.readFile(file.localFile);
  const uploadResponse = await fetch(upload.url, {
    method: "PUT",
    headers: upload.headers || { "Content-Type": file.contentType },
    body: bytes
  });
  if (!uploadResponse.ok) {
    throw new Error(`Upload failed for ${upload.path}: ${uploadResponse.status}`);
  }
}

console.error("finalizing...");
const finalizeHeaders = {
  "content-type": "application/json",
  "x-herenow-client": CLIENT
};
if (apiKey) finalizeHeaders.authorization = `Bearer ${apiKey}`;
await requestJson(create.upload.finalizeUrl, {
  method: "POST",
  headers: finalizeHeaders,
  body: JSON.stringify({ versionId: create.upload.versionId })
});

const slug = create.slug;
state.publishes ||= {};
state.publishes[slug] = {
  siteUrl: create.siteUrl
};
if (create.claimToken) state.publishes[slug].claimToken = create.claimToken;
if (create.claimUrl) state.publishes[slug].claimUrl = create.claimUrl;
if (create.expiresAt) state.publishes[slug].expiresAt = create.expiresAt;

await fs.mkdir(STATE_DIR, { recursive: true });
await fs.writeFile(STATE_FILE, `${JSON.stringify(state, null, 2)}\n`, "utf8");

const persistence = authMode === "anonymous" ? "expires_24h" : create.expiresAt ? "expires_at" : "permanent";
console.log(create.siteUrl);
console.error("");
console.error(`publish_result.site_url=${create.siteUrl}`);
console.error(`publish_result.auth_mode=${authMode}`);
console.error(`publish_result.api_key_source=${apiKeySource}`);
console.error(`publish_result.persistence=${persistence}`);
console.error(`publish_result.expires_at=${create.expiresAt || ""}`);
console.error(`publish_result.claim_url=${create.claimUrl?.startsWith("https://") ? create.claimUrl : ""}`);
