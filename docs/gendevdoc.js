const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType, ShadingType,
  TableOfContents, PageBreak, PageNumber, Header, Footer, TabStopType, TabStopPosition
} = require("docx");

const BLUE = "1F4E79", MID = "2E5A88", GREY = "666666", CODEBG = "F4F6F8";
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };

const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] });
function P(text, opts = {}) {
  const runs = Array.isArray(text) ? text : [new TextRun(text)];
  return new Paragraph({ children: runs, spacing: { after: 120 }, ...opts });
}
function bullet(text, level = 0) {
  const runs = Array.isArray(text) ? text : [new TextRun(text)];
  return new Paragraph({ numbering: { reference: "bullets", level }, children: runs, spacing: { after: 50 } });
}
function code(lines) {
  const arr = Array.isArray(lines) ? lines : lines.split("\n");
  return arr.map((ln, i) => new Paragraph({
    shading: { type: ShadingType.CLEAR, fill: CODEBG },
    spacing: { after: i === arr.length - 1 ? 140 : 0, before: i === 0 ? 40 : 0 },
    children: [new TextRun({ text: ln || " ", font: "Consolas", size: 17 })],
  }));
}
const b = (t) => new TextRun({ text: t, bold: true });
const tr = (t) => new TextRun(t);
const mono = (x) => new TextRun({ text: x, font: "Consolas", size: 17 });
function img(path, w, h, caption) {
  const out = [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 80, after: 40 },
    children: [new ImageRun({ type: "png", data: fs.readFileSync(path), transformation: { width: w, height: h } })] })];
  if (caption) out.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 },
    children: [new TextRun({ text: caption, italics: true, size: 17, color: GREY })] }));
  return out;
}
function table(headers, rows, widths) {
  const total = widths.reduce((a, c) => a + c, 0);
  const headRow = new TableRow({ tableHeader: true, children: headers.map((h, i) => new TableCell({
    borders, width: { size: widths[i], type: WidthType.DXA },
    shading: { type: ShadingType.CLEAR, fill: BLUE },
    margins: { top: 50, bottom: 50, left: 110, right: 110 },
    children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: "FFFFFF", size: 19 })] })] })) });
  const bodyRows = rows.map((r, ri) => new TableRow({ children: r.map((c, i) => new TableCell({
    borders, width: { size: widths[i], type: WidthType.DXA },
    shading: { type: ShadingType.CLEAR, fill: ri % 2 ? "FFFFFF" : "F7FAFC" },
    margins: { top: 50, bottom: 50, left: 110, right: 110 },
    children: [new Paragraph({ children: Array.isArray(c) ? c : [new TextRun({ text: String(c), size: 19 })] })] })) }));
  return new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: widths, rows: [headRow, ...bodyRows] });
}

const C = [];

// ===== Title =====
C.push(
  new Paragraph({ spacing: { before: 2400 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Solar Monitor", bold: true, size: 60, color: BLUE })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 },
    children: [new TextRun({ text: "Developer Documentation", size: 32, color: GREY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 2200 },
    children: [new TextRun({ text: "Architecture • Services • Data Model • API • Inverter Integration", size: 20, color: GREY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "dtitsolutions/solar-tracker  —  Version 1, June 2026", size: 20, color: GREY })] }),
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({ children: [new TextRun({ text: "Contents", bold: true, size: 30, color: BLUE })], spacing: { after: 160 } }),
  new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
  new Paragraph({ children: [new PageBreak()] }),
);

// ===== 1 Introduction =====
C.push(H1("1. Introduction"));
C.push(P("Solar Monitor is a self-hosted, brand-agnostic dashboard for solar inverters. It polls one or more inverters on the local network, stores time-series readings, and serves a live web dashboard — with no dependency on the manufacturer's cloud. This document is for developers extending or operating the codebase; for plain installation see the Deployment & Operations Guide."));
C.push(H2("1.1 Design goals"));
C.push(bullet([b("Local-first: "), tr("read inverters directly over the LAN; the cloud is optional.")]));
C.push(bullet([b("Fault isolation: "), tr("one OS process per inverter, so a single misbehaving device can't take down the fleet.")]));
C.push(bullet([b("Brand-agnostic core: "), tr("inverter access sits behind a driver interface so new protocols slot in.")]));
C.push(bullet([b("Operable: "), tr("Prometheus metrics, health endpoint, structured logs, and an in-app updater.")]));

// ===== 2 Architecture =====
C.push(H1("2. System Architecture"));
C.push(P("The system is a small set of containers orchestrated by Docker Compose. The browser only ever talks to the public web tier; the Python tier owns inverter access, persistence, and authentication."));
C.push(...img("/home/claude/diagrams/arch-system.png", 600, 336, "Figure 1 — Container topology and the connections between services."));
C.push(H2("2.1 Components"));
C.push(table(["Service", "Image / language", "Responsibility"],
  [
    [[mono("web")], "Node.js", "Public HTTP :8080; serves dashboard.html, streams live data over SSE, proxies all other /api calls to the Python tier."],
    [[mono("app")], "Python (stdlib http.server)", "REST API, authentication, the inverter supervisor, and a mirror thread that pushes snapshots to Redis."],
    [[mono("workers")], "Python (multiprocessing)", "One child process per inverter: connect, poll, decode, persist, emit."],
    [[mono("config-db")], "MariaDB", "Users, roles, inverter definitions, settings."],
    [[mono("data-db")], "MongoDB", "Time-series readings."],
    [[mono("redis")], "Redis", "Low-latency live bus between the collector and the web tier."],
  ], [1500, 2600, 5260]));
C.push(H2("2.2 Live data pipeline"));
C.push(P("Each poll cycle turns a raw inverter response into a normalized snapshot that is both stored (for history) and published (for live view)."));
C.push(...img("/home/claude/diagrams/arch-dataflow.png", 600, 230, "Figure 2 — From inverter read to browser render."));
C.push(P([b("Path: "), tr("a worker reads the inverter, decodes a snapshot, writes a reading document to MongoDB and publishes the snapshot to Redis; the web tier holds a Server-Sent-Events stream open to each browser and forwards new snapshots; the browser's "), mono("apply()"), tr(" binds values to the UI.")]));
C.push(H2("2.3 Process & thread model (app container)"));
C.push(...img("/home/claude/diagrams/arch-process.png", 560, 273, "Figure 3 — The supervisor spawns isolated worker processes; the API server and mirror thread run alongside."));
C.push(P([tr("The supervisor (in "), mono("solar_monitor.py"), tr(") spawns and monitors one "), mono("workers.py"), tr(" process per configured inverter. Workers hand snapshots back over a queue; a mirror thread relays the latest state to Redis. The HTTP API server runs in the main process. Because workers are separate OS processes, a crash or a wedged network read in one never blocks the others.")]));

// ===== 3 Tech stack =====
C.push(H1("3. Technology Stack"));
C.push(table(["Layer", "Choice", "Notes"],
  [
    ["Collector / API", "Python 3 (stdlib)", "http.server, multiprocessing, urllib — minimal dependencies"],
    ["Inverter driver", "goodwe (UDP) + Modbus TCP", "pluggable per protocol"],
    ["Web tier", "Node.js", "static serving + SSE + reverse proxy"],
    ["Frontend", "Vanilla JS + SVG", "single dashboard.html, no build step"],
    ["Config store", "MariaDB", "relational config/users"],
    ["Data store", "MongoDB", "schema-light time series"],
    ["Bus", "Redis", "pub/sub + last-state cache"],
    ["Packaging", "Docker Compose", "one command up; image bakes the git SHA"],
  ], [2400, 3000, 3960]));

// ===== 4 Repo layout =====
C.push(H1("4. Repository Layout"));
C.push(...code([
  "solar-tracker/",
  "├─ solar_monitor.py        # API, auth, supervisor, mirror, /metrics, /healthz",
  "├─ workers.py              # process-per-inverter: poll loop, daily() energy",
  "├─ config_store.py         # MariaDB: users, inverters, settings",
  "├─ data_store.py           # MongoDB: insert_reading, series()",
  "├─ logsetup.py             # structured logging + action/audit log",
  "├─ dashboard.html          # entire frontend (HTML/CSS/JS/SVG)",
  "├─ cli.py                  # standalone single-inverter diagnostic/reader",
  "├─ lib/",
  "│   └─ inverter_goodwe.py  # GoodWe driver: connect() + SUMMARY map",
  "├─ web/server.js           # Node web tier (SSE + proxy)",
  "├─ docker-compose.yml      # the stack",
  "├─ docker-compose.hostnet.yml  # host-network override (LAN edge cases)",
  "├─ Dockerfile              # app image; ARG GIT_SHA -> ENV SM_DEPLOYED_SHA",
  "├─ scripts/                # install-docker.sh, build.sh, update.sh, units, probe.py",
  "└─ docs/                   # this documentation",
]));

// ===== 5 Service reference =====
C.push(H1("5. Service Reference"));

C.push(H2("5.1 web/server.js (Node)"));
C.push(P("A thin public tier. It serves the dashboard, exposes the live endpoints from Redis, and proxies everything else to the Python API (which enforces its own auth)."));
C.push(bullet([mono("GET /"), tr(" → dashboard.html.")]));
C.push(bullet([mono("GET /api/stream"), tr(" → Server-Sent Events; emits the current snapshot then each update from Redis.")]));
C.push(bullet([mono("GET /api/data"), tr(" → the latest snapshot as JSON (one-shot).")]));
C.push(bullet([tr("Catch-all → reverse-proxy to "), mono("app:8080"), tr("; the Python tier handles auth and returns 401 when needed.")]));

C.push(H2("5.2 solar_monitor.py (API + supervisor)"));
C.push(P("The heart of the Python tier. Built on the standard-library HTTP server."));
C.push(H3("Request handling & auth"));
C.push(bullet([mono("do_GET"), tr(" / "), mono("do_POST"), tr(" dispatch on path. "), mono("/api/login"), tr(" is handled before the auth gate; all other routes require a valid session.")]));
C.push(bullet([mono("self._authed()"), tr(" validates the session cookie; "), mono("self._user()"), tr(" returns the username; "), mono("config_store.is_admin(self._user())"), tr(" gates admin-only routes.")]));
C.push(bullet([mono("self._json(code, payload)"), tr(", "), mono("self._read_json()"), tr(", "), mono("self._send(code, ctype, body)"), tr(" are the response helpers.")]));
C.push(H3("Supervisor & mirror"));
C.push(bullet("On startup it loads inverters from config and spawns a worker per inverter; it restarts a worker that dies and reaps it on shutdown."));
C.push(bullet("A mirror thread reads the newest per-inverter snapshot and writes combined live state to Redis for the web tier."));
C.push(H3("Update endpoints"));
C.push(bullet([mono("check_for_update()"), tr(" calls the GitHub commits API (token-aware), compares to the deployed SHA; "), mono("update_version()"), tr(" returns the deployed SHA only (no internet); "), mono("request_update()"), tr(" writes the trigger file.")]));

C.push(H2("5.3 workers.py (per-inverter process)"));
C.push(P("Each worker owns exactly one inverter. Pseudocode of the loop:"));
C.push(...code([
  "driver = lib.get_driver(brand)",
  "inverter = loop.run_until_complete(driver.connect(ip, retries=3))",
  "while not stop.is_set():",
  "    raw = loop.run_until_complete(inverter.read_runtime_data())",
  "    snap = normalize(raw)         # ppv, house_consumption, active_power, ...",
  "    data_store.insert_reading(inv_id, snap)   # history",
  "    daily(snap)                   # today's energy + grid integration",
  "    emit(snap, meta)              # -> supervisor queue -> redis",
  "    stop.wait(poll_interval)",
]));
C.push(bullet([mono("daily()"), tr(" maintains today's generation/consumption (native counters) and integrates measured grid power into import/export; it resets at local midnight.")]));
C.push(bullet([mono("_seed_grid_today()"), tr(" runs once on (re)start: it integrates today's already-logged readings so a mid-day restart doesn't zero the day's grid totals.")]));

C.push(H2("5.4 config_store.py (MariaDB) & data_store.py (MongoDB)"));
C.push(bullet([mono("config_store"), tr(": users (hashed passwords, role), inverters (id, nickname, brand, ip), and settings; helpers like "), mono("is_admin()"), tr(", "), mono("set_password()"), tr(".")]));
C.push(bullet([mono("data_store"), tr(": "), mono("insert_reading(inverter_id, snap)"), tr(" maps "), mono("active_power → grid_power_w"), tr(" (+import/−export) and stores "), mono("recorded_at"), tr(" in UTC; "), mono("series(inverter_id, since, until, limit)"), tr(" returns rows oldest-first for history and seeding.")]));

C.push(H2("5.5 lib/inverter_goodwe.py (driver)"));
C.push(P([tr("Exposes "), mono("async connect(ip, retries)"), tr(" and a "), mono("SUMMARY"), tr(" map of the sensor ids the dashboard cares about. The connect routine tries an explicit family hint ("), mono("SM_GOODWE_FAMILY"), tr("), then auto-detect, then a sweep of ES/ET/EH/DT/BP/EM — making GoodWe's flaky discovery reliable. See Section 7 for the Modbus-TCP path.")]));

// ===== 6 Data model =====
C.push(H1("6. Data Model"));
C.push(H2("6.1 Reading snapshot (normalized keys)"));
C.push(P("The canonical keys produced by a worker and consumed by the dashboard:"));
C.push(table(["Key", "Meaning", "Sign / unit"],
  [
    [[mono("ppv")], "PV / solar power", "W"],
    [[mono("house_consumption")], "House load", "W"],
    [[mono("active_power")], "Grid power", "+ import / − export, W"],
    [[mono("pbattery1")], "Battery power", "+ charge / − discharge, W"],
    [[mono("battery_soc")], "Battery state of charge", "%"],
    [[mono("e_day")], "Generation today", "kWh"],
    [[mono("e_load_day")], "Consumption today", "kWh"],
    [[mono("e_total")], "Lifetime generation", "kWh"],
    [[mono("temperature")], "Inverter temperature", "°C"],
  ], [2700, 4060, 2600]));
C.push(H2("6.2 MongoDB reading document"));
C.push(...code([
  "{ inverter_id: <id>,",
  "  recorded_at: <UTC ISO>,        // indexed for range queries",
  "  grid_power_w: <+imp/-exp>,     // derived from active_power",
  "  solar_power_w, load_power_w, battery_power_w, battery_soc, ... }",
]));
C.push(H2("6.3 Redis live state"));
C.push(P("The mirror thread keeps the newest combined snapshot under a well-known key and publishes updates on a channel the web tier subscribes to. The web tier never reads the databases directly."));

// ===== 7 (overflow into API) =====
C.push(H1("7. HTTP API Reference"));
C.push(P("All routes are served by the Python tier (proxied through the web tier). JSON in/out. Admin-only routes return 403 for non-admins; unauthenticated requests return 401."));
C.push(table(["Method & path", "Auth", "Purpose"],
  [
    [[mono("POST /api/login")], "public", "create a session"],
    [[mono("POST /api/logout")], "user", "end the session"],
    [[mono("GET /api/me")], "user", "current user + role"],
    [[mono("GET /api/data")], "user", "latest snapshot"],
    [[mono("GET /api/stream")], "user", "SSE live stream"],
    [[mono("GET /api/history")], "user", "windowed time-series for the chart"],
    [[mono("GET /api/inverters")], "user", "list inverters"],
    [[mono("POST /api/inverters")], "admin", "add / edit / remove an inverter"],
    [[mono("GET/POST /api/users…")], "admin", "user administration"],
    [[mono("GET /api/settings")], "user", "server settings"],
    [[mono("GET /api/update/check")], "admin", "compare deployed vs GitHub"],
    [[mono("GET /api/update/version")], "admin", "deployed SHA (no internet)"],
    [[mono("POST /api/update/apply")], "admin", "trigger a self-update"],
    [[mono("GET /metrics")], "open*", "Prometheus metrics"],
    [[mono("GET /healthz")], "open*", "health probe"],
  ], [3200, 1500, 4660]));
C.push(P([new TextRun({ text: "* metrics/health are intended for an internal network; restrict at the reverse proxy if exposed.", italics: true, size: 18, color: GREY })]));

// ===== 8 Inverter integration =====
C.push(H1("8. Inverter Integration"));
C.push(P("The most important — and most device-specific — part of the system. GoodWe dongles speak one of two local protocols depending on generation."));
C.push(...img("/home/claude/diagrams/arch-connect.png", 470, 269, "Figure 4 — Choosing the driver from what the dongle exposes."));
C.push(H2("8.1 Protocols"));
C.push(table(["Protocol", "Transport", "Framing", "Library / code"],
  [
    ["GoodWe UDP", "UDP 8899", "AA55 (ES) or Modbus-RTU-over-UDP (ET)", "goodwe package"],
    ["Modbus TCP", "TCP 502", "Modbus TCP (MBAP + PDU)", "custom reader (cli.py / planned driver)"],
  ], [2200, 1800, 3200, 2160]));
C.push(H2("8.2 connect() strategy (UDP)"));
C.push(...code([
  "order = [hint] if hint else []",
  "order += [None]                       # auto-discovery",
  "order += ['ES','ET','EH','DT','BP','EM']   # ES first (GW####ES)",
  "for fam in order:",
  "    try: return goodwe.connect(host, family=fam, timeout, retries)",
  "    except: keep trying",
]));
C.push(H2("8.3 Modbus TCP (newer dongles)"));
C.push(P([tr("Newer dongles — the kind the SEMS+ app connects to locally — answer on TCP 502 with Modbus. A read is an MBAP header + PDU (function 3). Device info lives at "), mono("0x88B8"), tr(" (model, serial at byte offset 6, rated power); running data starts at "), mono("0x891C"), tr("; battery SoC is in the BMS block ("), mono("0x908F"), tr("). The device-info serial provides a built-in decode check.")]));
C.push(...code([
  "# Modbus-TCP read (function 3), no external deps",
  "pdu = struct.pack('>BHH', 0x03, start, qty)",
  "adu = struct.pack('>HHHB', 1, 0, len(pdu)+1, unit) + pdu   # unit 0xF7",
  "# response: MBAP(7) + func(1) + bytecount(1) + data",
]));
C.push(H2("8.4 Adding a driver"));
C.push(P([tr("Drivers live in "), mono("lib/"), tr(" and are resolved by "), mono("lib.get_driver(brand)"), tr(". A driver provides an async "), mono("connect(ip, retries)"), tr(" returning an object with "), mono("read_runtime_data()"), tr(", "), mono("sensors()"), tr(", "), mono("model_name"), tr(" and "), mono("serial_number"), tr(". To add Modbus-TCP support, implement a driver that wraps the reads above and normalizes to the keys in Section 6.1.")]));

// ===== 9 energy conventions =====
C.push(H1("9. Energy & Sign Conventions"));
C.push(bullet([b("Grid "), mono("active_power"), tr(": positive = importing from grid, negative = exporting.")]));
C.push(bullet([b("Battery "), mono("pbattery1"), tr(": positive = charging, negative = discharging.")]));
C.push(bullet([b("Daily import/export "), tr("are integrated from measured grid power (not the inverter's native daily counters, which are unreliable on this ES unit), and seeded from logged data on restart.")]));
C.push(bullet([b("Generation/consumption today "), tr("use the inverter's native "), mono("e_day"), tr(" / "), mono("e_load_day"), tr(" counters.")]));

// ===== 10 frontend =====
C.push(H1("10. Frontend Architecture"));
C.push(P([tr("The whole UI is one "), mono("dashboard.html"), tr(" — HTML, CSS and vanilla JS, no build step. It is a single-page app with an admin shell (sidebar + topbar + content).")]));
C.push(bullet([b("Pages: "), mono("showPage(id)"), tr(" toggles "), mono(".page.active"), tr(" (Dashboard, History, Fleet, Inverter Info, Settings).")]));
C.push(bullet([b("Live binding: "), mono("apply(snapshot)"), tr(" updates KPI cards, the flow diagram, and the 3D house from one data object so the views never disagree.")]));
C.push(bullet([b("Flow views: "), tr("a toggle switches the circles diagram and the isometric 3D house; the choice is remembered in localStorage.")]));
C.push(bullet([b("Updates UI: "), tr("an admin-only Updates panel and a global banner call the update endpoints, with a browser-side GitHub fallback.")]));
C.push(bullet([b("Verification: "), tr("the frontend is checked with "), mono("node --check"), tr(" and a DOM-shim harness that loads the script and asserts no init errors.")]));

// ===== 11 auth =====
C.push(H1("11. Authentication & Roles"));
C.push(P("Sessions are cookie-based and validated on every request by the Python tier. Two roles: admin (full control, including users, inverters and updates) and user (read the dashboard). The default admin/admin must be changed on first login. Non-admin UI is hidden via a body.role-user CSS class and enforced server-side."));

// ===== 12 build/deploy/update =====
C.push(H1("12. Build, Deploy & Self-Update"));
C.push(P([tr("The image bakes the current commit via "), mono("Dockerfile ARG GIT_SHA → ENV SM_DEPLOYED_SHA"), tr(", so the running app knows its own version. "), mono("build.sh"), tr(" passes "), mono("GIT_SHA=$(git rev-parse HEAD)"), tr(".")]));
C.push(...img("/home/claude/diagrams/arch-deploy.png", 600, 201, "Figure 5 — A container can't rebuild itself, so the app signals a host-side watcher."));
C.push(P([tr("On \"Update now\", the app writes "), mono(".update/request"), tr(" (a bind-mounted directory). A systemd "), mono(".path"), tr(" unit watches that file and runs "), mono("update.sh"), tr(": "), mono("git pull → docker compose build → up -d"), tr(", then records the new SHA and clears the trigger. No Docker socket is exposed to the container.")]));

// ===== 13 observability =====
C.push(H1("13. Observability"));
C.push(bullet([mono("/metrics"), tr(" — Prometheus counters/gauges (poll success/failure, connection state, last-read age).")]));
C.push(bullet([mono("/healthz"), tr(" — liveness/health for probes and uptime checks.")]));
C.push(bullet([tr("Structured logs via "), mono("logsetup.py"), tr(", including an action/audit log for admin operations (logins, user changes, update requests).")]));

// ===== 14 testing =====
C.push(H1("14. Testing & Verification"));
C.push(P("The sandbox used to build this has no network, Docker or real databases, so verification leans on fast static and stand-in checks:"));
C.push(bullet([mono("python -m py_compile"), tr(" for every Python module.")]));
C.push(bullet([mono("node --check"), tr(" on the extracted dashboard script.")]));
C.push(bullet([tr("A DOM-shim harness that loads the dashboard JS against mock elements/fetch and asserts initialization runs without errors and dropdowns populate.")]));
C.push(bullet("In-memory stand-ins for config_store/data_store, and unit tests for the tricky logic (grid-seeding integration, SHA comparison, Modbus framing/decoding)."));

// ===== 15 extending =====
C.push(H1("15. Extending the System"));
C.push(bullet([b("New inverter brand/protocol: "), tr("add a driver under "), mono("lib/"), tr(" and register it with "), mono("lib.get_driver"), tr("; normalize to the Section 6.1 keys.")]));
C.push(bullet([b("New dashboard view: "), tr("add a "), mono(".page"), tr(", a nav item with "), mono("data-page"), tr(", and bind data in "), mono("apply()"), tr(".")]));
C.push(bullet([b("New metric: "), tr("emit it from the worker snapshot and expose it on "), mono("/metrics"), tr(".")]));
C.push(bullet([b("Run from source: "), tr("use "), mono("cli.py"), tr(" to validate inverter access before wiring a new driver into the stack.")]));

// ===== 16 appendix =====
C.push(H1("16. Appendix — Environment Variables"));
C.push(table(["Variable", "Default", "Purpose"],
  [
    [[mono("SM_GOODWE_FAMILY")], "(empty)", "Force a UDP protocol family (e.g. ES)"],
    [[mono("SM_GOODWE_TIMEOUT")], "2", "Per-read timeout (s)"],
    [[mono("SM_GITHUB_REPO")], "dtitsolutions/solar-tracker", "Repo the updater checks"],
    [[mono("SM_GITHUB_BRANCH")], "main", "Branch tracked"],
    [[mono("SM_GITHUB_TOKEN")], "(empty)", "Token for a private repo"],
    [[mono("SM_DEPLOYED_SHA")], "(build arg)", "Commit baked into the image"],
    [[mono("SM_UPDATE_TRIGGER")], "/app/.update/request", "Self-update trigger file"],
    [[mono("SM_OFFLINE_TIMEOUT_SECONDS")], "120", "Mark an inverter offline after silence"],
  ], [3400, 2600, 3360]));
C.push(P([new TextRun({ text: "— End of developer documentation —", italics: true, color: GREY })], { alignment: AlignmentType.CENTER, spacing: { before: 360 } }));

// ===== assemble =====
const doc = new Document({
  creator: "Solar Monitor", title: "Solar Monitor — Developer Documentation",
  styles: {
    default: { document: { run: { font: "Arial", size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, color: BLUE, font: "Arial" },
        paragraph: { spacing: { before: 300, after: 140 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, color: MID, font: "Arial" },
        paragraph: { spacing: { before: 200, after: 90 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, color: "333333", font: "Arial" },
        paragraph: { spacing: { before: 150, after: 70 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 700, hanging: 340 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 4 } },
      children: [new TextRun({ text: "Solar Monitor — Developer Documentation", size: 15, color: GREY })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      children: [new TextRun({ text: "dtitsolutions/solar-tracker", size: 15, color: GREY }),
        new TextRun({ text: "\t", size: 15 }),
        new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], size: 15, color: GREY })] })] }) },
    children: C,
  }],
});
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync("/home/claude/Solar-Monitor-Developer-Docs.docx", buf); console.log("written"); });
