/**
 * Prismatica 前端 Node 客户端示例
 *
 * 运行:
 *   node examples/node_client.js
 * 环境变量:
 *   PRISMATICA_BASE_URL    默认 http://localhost:8000
 *   PRISMATICA_INV_CODE    激活码(可选)
 */
const BASE_URL = process.env.PRISMATICA_BASE_URL || "http://localhost:8000";

// 浏览器可用 crypto.randomUUID;Node 17+ 同样支持
const DEVICE_ID = (crypto.randomUUID && crypto.randomUUID())
  || require("crypto").randomUUID();

let accessToken = "";
let refreshToken = "";

function authHeaders(extra = {}) {
  return {
    "Content-Type": "application/json",
    "X-Client-Platform": "node",
    "X-Device-Id": DEVICE_ID,
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    ...extra,
  };
}

async function api(method, path, body, extraHeaders) {
  const init = { method, headers: authHeaders(extraHeaders) };
  if (body) init.body = JSON.stringify(body);
  const res = await fetch(`${BASE_URL}${path}`, init);
  const text = await res.text();
  const data = (() => { try { return JSON.parse(text); } catch { return text; } })();
  console.log(`[${res.status}] ${method} ${path}`, JSON.stringify(data, null, 2));
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${data?.error?.message || text}`);
  return data;
}

async function redeem(code) {
  const data = await api("POST", "/v1/auth/redeem", {
    code,
    deviceId: DEVICE_ID,
    deviceName: "node-demo",
    displayName: "node",
  });
  accessToken  = data.accessToken;
  refreshToken = data.refreshToken;
  return data;
}

async function me()    { return api("GET", "/v1/account/me"); }
async function bills() { return api("GET", "/v1/account/bills?limit=10"); }

async function refresh() {
  const data = await api("POST", "/v1/auth/refresh", { refreshToken });
  accessToken  = data.accessToken;
  refreshToken = data.refreshToken;
  return data;
}

async function main() {
  const code = process.env.PRISMATICA_INV_CODE;
  if (code) {
    console.log("→ Redeem");
    await redeem(code);
  } else if (refreshToken) {
    console.log("→ Refresh");
    await refresh();
  } else {
    console.log("Set PRISMATICA_INV_CODE to call /v1/auth/redeem first.");
  }

  console.log("\n→ /v1/account/me");
  await me();

  console.log("\n→ /v1/account/bills");
  try { await bills(); } catch { /* 没账单也正常 */ }
}

main().catch((e) => { console.error(e); process.exit(1); });
