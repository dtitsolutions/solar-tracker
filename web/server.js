// Solar Monitor — dedicated web tier (Node).
//
// Serves the live stream straight from Redis (fast, push-based, no refresh) and
// proxies everything else (login, dashboard, settings, users) to the Python
// collector/API. Auth stays centralised in Python: for the live endpoints we
// validate the caller's session by asking the backend's /api/me.

const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");
const Redis = require("ioredis");

const PORT = parseInt(process.env.PORT || "8080", 10);
const BACKEND = process.env.SM_BACKEND_URL || "http://app:8080";
const REDIS_HOST = process.env.SM_REDIS_HOST || "redis";
const REDIS_PORT = parseInt(process.env.SM_REDIS_PORT || "6379", 10);
const LIVE_KEY = process.env.SM_REDIS_KEY || "sm:live";
const LIVE_CHANNEL = process.env.SM_REDIS_CHANNEL || "sm:live";

const redis = new Redis({ host: REDIS_HOST, port: REDIS_PORT, maxRetriesPerRequest: 2, lazyConnect: false });
redis.on("error", (e) => console.error("[redis]", e.message));

const app = express();
app.disable("x-powered-by");

// Validate the session cookie by asking the Python backend.
async function isAuthed(req) {
  try {
    const r = await fetch(`${BACKEND}/api/me`, { headers: { cookie: req.headers.cookie || "" } });
    return r.status === 200;
  } catch (_) {
    return false;
  }
}

// Live snapshot from the Redis cache; fall through to the backend if empty.
app.get("/api/data", async (req, res, next) => {
  if (!(await isAuthed(req))) return res.status(401).json({ ok: false, error: "auth required" });
  try {
    const cached = await redis.get(LIVE_KEY);
    if (cached) {
      res.set("Content-Type", "application/json");
      res.set("Cache-Control", "no-store");
      return res.send(cached);
    }
  } catch (_) { /* fall through */ }
  return next();
});

// Live stream over SSE, pushed from Redis pub/sub. No polling, no refresh.
app.get("/api/stream", async (req, res) => {
  if (!(await isAuthed(req))) return res.status(401).json({ ok: false, error: "auth required" });
  res.set({ "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
  res.flushHeaders();

  try {
    const current = await redis.get(LIVE_KEY);
    if (current) res.write(`data: ${current}\n\n`);
  } catch (_) { /* ignore */ }

  const sub = redis.duplicate();
  sub.on("error", (e) => console.error("[redis-sub]", e.message));
  sub.subscribe(LIVE_CHANNEL).catch((e) => console.error("[redis-sub]", e.message));
  sub.on("message", (_channel, message) => res.write(`data: ${message}\n\n`));

  const ping = setInterval(() => res.write(": ping\n\n"), 15000);
  req.on("close", () => { clearInterval(ping); sub.quit().catch(() => {}); });
});

// Everything else -> Python backend (login, dashboard, settings, users, ...).
app.use(createProxyMiddleware({ target: BACKEND, changeOrigin: true, ws: true, xfwd: true }));

app.listen(PORT, () =>
  console.log(`Solar Monitor web (Node) :${PORT} -> ${BACKEND}, redis ${REDIS_HOST}:${REDIS_PORT}`));
