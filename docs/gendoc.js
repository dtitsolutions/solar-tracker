const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType, ShadingType,
  TableOfContents, PageBreak, PageNumber, Header, Footer, TabStopType, TabStopPosition
} = require("docx");

const BLUE = "1F4E79", LIGHT = "D5E8F0", GREY = "666666", CODEBG = "F2F2F2";
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };

// ---------- helpers ----------
const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] });

function P(text, opts = {}) {
  const runs = Array.isArray(text) ? text : [new TextRun(text)];
  return new Paragraph({ children: runs, spacing: { after: 120 }, ...opts });
}
function bullet(text, level = 0) {
  const runs = Array.isArray(text) ? text : [new TextRun(text)];
  return new Paragraph({ numbering: { reference: "bullets", level }, children: runs, spacing: { after: 60 } });
}
function num(text, level = 0) {
  const runs = Array.isArray(text) ? text : [new TextRun(text)];
  return new Paragraph({ numbering: { reference: "steps", level }, children: runs, spacing: { after: 60 } });
}
function code(lines) {
  const arr = Array.isArray(lines) ? lines : lines.split("\n");
  return arr.map((ln, i) =>
    new Paragraph({
      shading: { type: ShadingType.CLEAR, fill: CODEBG },
      spacing: { after: i === arr.length - 1 ? 120 : 0, before: i === 0 ? 40 : 0 },
      children: [new TextRun({ text: ln || " ", font: "Consolas", size: 18 })],
    })
  );
}
function b(t) { return new TextRun({ text: t, bold: true }); }
function t(t) { return new TextRun(t); }
function mono(x) { return new TextRun({ text: x, font: "Consolas", size: 18 }); }

function table(headers, rows, widths) {
  const total = widths.reduce((a, c) => a + c, 0);
  const headRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders, width: { size: widths[i], type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: BLUE },
      margins: { top: 60, bottom: 60, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: "FFFFFF" })] })],
    })),
  });
  const bodyRows = rows.map((r, ri) => new TableRow({
    children: r.map((c, i) => new TableCell({
      borders, width: { size: widths[i], type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: ri % 2 ? "FFFFFF" : "F7FAFC" },
      margins: { top: 60, bottom: 60, left: 120, right: 120 },
      children: [new Paragraph({ children: Array.isArray(c) ? c : [new TextRun(String(c))] })],
    })),
  }));
  return new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: widths, rows: [headRow, ...bodyRows] });
}

// ---------- content ----------
const children = [];

// Title page
children.push(
  new Paragraph({ spacing: { before: 2600, after: 0 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Solar Monitor", bold: true, size: 64, color: BLUE })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: "Self-Hosted Solar Inverter Dashboard", size: 30, color: GREY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
    children: [new TextRun({ text: "Deployment & Operations Guide", size: 26 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 2400 },
    children: [new TextRun({ text: "GoodWe GW6000ES • dtitsolutions/solar-tracker", size: 20, color: GREY })] }),
  new Paragraph({ alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Version 1 — June 2026", size: 20, color: GREY })] }),
  new Paragraph({ children: [new PageBreak()] }),
);

// TOC
children.push(
  new Paragraph({ children: [new TextRun({ text: "Contents", bold: true, size: 32, color: BLUE })], spacing: { after: 200 } }),
  new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
  new Paragraph({ children: [new PageBreak()] }),
);

// 1. Overview
children.push(H1("1. Overview"));
children.push(P("Solar Monitor is a self-hosted dashboard that reads a solar inverter on your local network and shows live power flow, history, and per-inverter detail — with no dependency on the manufacturer's cloud. It runs as a small Docker stack on a machine on the same network as the inverter."));
children.push(P([b("What you get: "), t("a live power-flow view (2D circles and a 3D house), a history chart, a multi-inverter fleet view, multi-user logins with roles, Prometheus metrics, and an in-app updater that pulls new versions from GitHub.")]));
children.push(H2("1.1 Architecture at a glance"));
children.push(P("The stack is composed of small services, each in its own container:"));
children.push(table(
  ["Service", "Role", "Port"],
  [
    ["web (Node)", "Public web tier; serves the dashboard, streams live data (SSE), proxies the API", "8080"],
    ["app (Python)", "Collector + API; one worker process per inverter; enforces auth", "internal"],
    ["config-db (MariaDB)", "Configuration, users, inverters", "internal (phpMyAdmin 8081)"],
    ["data-db (MongoDB)", "Time-series readings", "internal (mongo-express 8082)"],
    ["redis", "Live data bus between collector and web tier", "internal"],
  ],
  [1900, 5260, 2200]
));
children.push(P([b("Note on the inverter link: "), t("the collector talks to the inverter directly over the LAN. Older GoodWe WiFi/LAN modules use UDP port 8899; newer dongles (the kind the SEMS+ app connects to locally) use Modbus TCP on port 502. See Section 6.")]));

// 2. Requirements
children.push(H1("2. Requirements"));
children.push(bullet("A 64-bit Linux host (AlmaLinux/Rocky/RHEL or Debian/Ubuntu) on the same LAN/subnet as the inverter."));
children.push(bullet("Docker Engine with the Compose plugin (installed in Section 3)."));
children.push(bullet("Roughly 2 GB RAM and 5 GB free disk for the stack and its databases."));
children.push(bullet("The inverter's IP address, and outbound internet only if you want the in-app updater."));

// 3. Installing Docker
children.push(H1("3. Installing Docker"));
children.push(P([t("You need Docker Engine plus the Compose plugin (the "), mono("docker compose"), t(" subcommand). The bundled script does it for you on both RHEL-family and Debian/Ubuntu hosts.")]));
children.push(H2("3.1 The easy way — install-docker.sh"));
children.push(...code([
  "sudo ./scripts/install-docker.sh",
]));
children.push(P("The script: removes conflicting old packages, adds Docker's official repository, installs Engine + CLI + Compose + Buildx, enables the service at boot, adds your user to the docker group, and (on RHEL) opens the Docker bridge through firewalld."));
children.push(P([b("After it finishes: "), t("log out and back in (or run "), mono("newgrp docker"), t(") so you can run Docker without sudo.")]));

children.push(H2("3.2 Manual — AlmaLinux / Rocky / RHEL"));
children.push(...code([
  "sudo dnf -y install dnf-plugins-core",
  "sudo dnf config-manager --add-repo \\",
  "     https://download.docker.com/linux/centos/docker-ce.repo",
  "sudo dnf install -y docker-ce docker-ce-cli containerd.io \\",
  "     docker-buildx-plugin docker-compose-plugin",
  "sudo systemctl enable --now docker",
  "sudo usermod -aG docker \"$USER\"      # then log out/in",
]));
children.push(H2("3.3 Manual — Ubuntu / Debian"));
children.push(...code([
  "sudo apt-get update && sudo apt-get install -y ca-certificates curl",
  "sudo install -m 0755 -d /etc/apt/keyrings",
  "sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \\",
  "     -o /etc/apt/keyrings/docker.asc",
  "echo \"deb [arch=$(dpkg --print-architecture) \\",
  "  signed-by=/etc/apt/keyrings/docker.asc] \\",
  "  https://download.docker.com/linux/ubuntu \\",
  "  $(. /etc/os-release && echo $VERSION_CODENAME) stable\" \\",
  "  | sudo tee /etc/apt/sources.list.d/docker.list",
  "sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli \\",
  "     containerd.io docker-buildx-plugin docker-compose-plugin",
  "sudo systemctl enable --now docker",
]));
children.push(H2("3.4 Verify"));
children.push(...code(["docker --version", "docker compose version", "docker run --rm hello-world"]));

// 4. Deploying
children.push(H1("4. Deploying Solar Monitor"));
children.push(P([t("Place the project at "), mono("/opt/solar-tracker"), t(" (a git checkout of "), mono("dtitsolutions/solar-tracker"), t("), then build and start it with the bundled script.")]));
children.push(H2("4.1 The easy way — build.sh"));
children.push(...code([
  "cd /opt/solar-tracker",
  "./scripts/build.sh            # build + start (detached)",
  "./scripts/build.sh --no-cache # force a clean rebuild",
  "./scripts/build.sh --logs     # build, start, then follow logs",
]));
children.push(P("build.sh bakes the current git commit into the image (so the in-app updater knows what is deployed), builds the images, starts the stack, records the deployed version, and prints the dashboard URL."));
children.push(H2("4.2 Manual"));
children.push(...code([
  "cd /opt/solar-tracker",
  "GIT_SHA=\"$(git rev-parse HEAD)\" docker compose up -d --build",
]));
children.push(H2("4.3 First login"));
children.push(P([t("Open "), mono("http://<server-ip>:8080"), t(" and sign in with "), b("admin / admin"), t(". Change the password immediately under Settings. Add your inverter under Settings, giving it a name and IP.")]));
children.push(P([b("Companion tools: "), mono("http://<server-ip>:8081"), t(" (phpMyAdmin, config DB) and "), mono("http://<server-ip>:8082"), t(" (mongo-express, readings).")]));
children.push(H2("4.4 Everyday commands"));
children.push(...code([
  "docker compose ps                 # service status",
  "docker compose logs -f app        # collector logs",
  "docker compose restart app        # restart the collector",
  "docker compose down               # stop the stack",
]));

// 5. (moved) Connecting your inverter
children.push(H1("5. Connecting Your Inverter"));
children.push(P("GoodWe inverters expose a local interface through their WiFi/LAN dongle. There are two different protocols depending on the dongle generation, and using the wrong one is the most common reason for \"unable to connect\" even when the IP pings."));
children.push(table(
  ["Dongle generation", "Local protocol", "Port", "How to read it"],
  [
    ["Older WiFi/LAN kit", "GoodWe UDP (AA55 / Modbus-RTU over UDP)", "UDP 8899", "Built-in goodwe driver (auto/family)"],
    ["Newer dongle (SEMS+ local)", "Modbus TCP", "TCP 502", "Modbus-TCP driver"],
  ],
  [2600, 3500, 1400, 2860]
));
children.push(P([b("Important: "), t("a "), mono("ping"), t(" or "), mono("nc -u"), t(" \"success\" does not prove the inverter is answering — UDP is connectionless, so "), mono("nc -u"), t(" reports \"Connected\" for any routable IP. Use the CLI (Section 7) to confirm what the dongle actually speaks.")]));
children.push(H2("5.1 Which one do I have?"));
children.push(P("Run the scanner against the inverter IP:"));
children.push(...code([".venv/bin/python cli.py scan 192.168.10.51"]));
children.push(bullet([b("TCP 502 open"), t(" → newer dongle, Modbus TCP. Confirm with "), mono("cli.py modbus <ip>"), t(" and read it with "), mono("cli.py tcp <ip>"), t(".")]));
children.push(bullet([b("UDP 8899 replies"), t(" → older module; the built-in goodwe driver works. If auto-detect fails, set "), mono("SM_GOODWE_FAMILY=ES"), t(" for a GW####ES.")]));
children.push(P([b("This deployment (GW6000ES with a newer dongle) uses Modbus TCP on port 502."), t(" The device-info read returns the inverter's real serial, which confirms decoding end-to-end.")]));
children.push(H2("5.2 Useful environment knobs"));
children.push(table(
  ["Variable", "Purpose", "Example"],
  [
    ["SM_GOODWE_FAMILY", "Skip UDP auto-detect; force a protocol family", "ES"],
    ["SM_GOODWE_TIMEOUT", "Per-read timeout (seconds)", "2"],
    ["SM_GITHUB_REPO", "Repo the updater checks", "dtitsolutions/solar-tracker"],
    ["SM_GITHUB_BRANCH", "Branch the updater tracks", "main"],
    ["SM_GITHUB_TOKEN", "Token for a private repo", "github_pat_…"],
  ],
  [2900, 4360, 2100]
));

// 6. Updates
children.push(H1("6. In-App Updates"));
children.push(P("Settings → Updates lets an admin check GitHub for a newer commit and apply it. Because a container cannot rebuild its own stack, the app drops a trigger file that a small host-side systemd watcher acts on."));
children.push(H2("6.1 One-time host setup"));
children.push(...code([
  "cd /opt/solar-tracker",
  "sudo cp scripts/solar-tracker-update.service /etc/systemd/system/",
  "sudo cp scripts/solar-tracker-update.path    /etc/systemd/system/",
  "sudo systemctl daemon-reload",
  "sudo systemctl enable --now solar-tracker-update.path",
  "GIT_SHA=\"$(git rev-parse HEAD)\" docker compose up -d --build",
]));
children.push(H2("6.2 How it works"));
children.push(bullet([b("Check: "), t("the app asks GitHub for the latest commit and compares it to the deployed SHA. If the server has no internet, the browser does the comparison instead.")]));
children.push(bullet([b("Notify: "), t("a banner appears when a newer commit exists.")]));
children.push(bullet([b("Apply: "), t("writes "), mono(".update/request"), t("; the systemd path unit runs "), mono("scripts/update.sh"), t(" which pulls, rebuilds, restarts, and records the new version.")]));
children.push(P([b("Private repo: "), t("set "), mono("SM_GITHUB_TOKEN"), t(" so the check works, and give the "), mono("/opt/solar-tracker"), t(" checkout working git credentials (SSH deploy key or a token in the remote URL) so the pull works.")]));

// 7. CLI
children.push(H1("7. The Inverter CLI (cli.py)"));
children.push(P("cli.py talks to one inverter directly — no Docker, DB or web stack — which makes it the fastest way to diagnose connectivity and confirm the protocol. Run it on the host in a virtualenv:"));
children.push(...code([
  "python3 -m venv .venv",
  ".venv/bin/pip install goodwe",
  ".venv/bin/python cli.py read 192.168.10.51",
]));
children.push(table(
  ["Command", "What it does"],
  [
    [[mono("read <ip>")], "Connect (UDP) and print live readings; --family ES to force, --all, --json"],
    [[mono("families <ip>")], "Try every UDP family; report which connects"],
    [[mono("probe <ip>")], "Raw UDP reachability (ES AA55 + ET Modbus frames); no goodwe needed"],
    [[mono("wifi")], "Find GoodWe modules via UDP 48899 broadcast — reveals the current IP"],
    [[mono("scan <ip>")], "Scan TCP 502/8899 + UDP 8899/48899 to identify the interface"],
    [[mono("modbus <ip>")], "Probe Modbus TCP :502 (newer dongles)"],
    [[mono("tcp <ip>")], "Read a Modbus-TCP inverter: device info + live values (--debug dumps registers)"],
  ],
  [2300, 7060]
));
children.push(P([b("Tip: "), t("the inverter's local interface allows only one client at a time. Close the SEMS+ app (and stop the stack with "), mono("docker compose down"), t(") before running the CLI.")]));

// 8. Troubleshooting
children.push(H1("8. Troubleshooting"));
children.push(H2("8.1 \"Unable to connect to inverter\""));
children.push(num([b("Confirm the protocol. "), t("Run "), mono("cli.py scan <ip>"), t(". TCP 502 open = Modbus TCP; UDP 8899 reply = older module.")]));
children.push(num([b("Check the IP. "), t("Run "), mono("cli.py wifi"), t(". If the module answers at a different IP, DHCP moved it — use the new IP and set a reservation.")]));
children.push(num([b("Free the session. "), t("Close SEMS+ and stop other pollers; only one local client is allowed.")]));
children.push(num([b("Container vs host. "), t("If the CLI works on the host but the dashboard does not, it is Docker networking (see 8.2).")]));
children.push(num([b("Power-cycle. "), t("If a previously-working module goes silent, reboot the inverter / reseat the dongle.")]));
children.push(H2("8.2 Container can't reach the LAN or internet (AlmaLinux)"));
children.push(P("On RHEL-family hosts, firewalld often blocks traffic forwarded from the Docker bridge to other LAN hosts and the internet. Open it:"));
children.push(...code([
  "sudo firewall-cmd --permanent --add-masquerade",
  "sudo firewall-cmd --permanent --zone=trusted --add-interface=docker0",
  "sudo firewall-cmd --reload",
  "sudo systemctl restart docker",
]));
children.push(P([t("If the compose bridge is not "), mono("docker0"), t(", find it with "), mono("ip -o link | grep br-"), t(" and add that interface to the trusted zone. As an alternative, run the collector on the host network: "), mono("docker compose -f docker-compose.yml -f docker-compose.hostnet.yml up -d"), t(".")]));
children.push(H2("8.3 GitHub check fails"));
children.push(bullet([b("Private repo "), t("→ set "), mono("SM_GITHUB_TOKEN"), t(".")]));
children.push(bullet([b("No server internet "), t("→ the browser-side check still works for a public repo.")]));

// 9. Reference
children.push(H1("9. Reference"));
children.push(H2("9.1 Ports"));
children.push(table(
  ["Port", "Service"],
  [["8080", "Dashboard (web)"], ["8081", "phpMyAdmin (config DB)"], ["8082", "mongo-express (readings)"],
   ["502 (TCP)", "Inverter Modbus TCP (newer dongles)"], ["8899 (UDP)", "Inverter GoodWe protocol (older modules)"],
   ["48899 (UDP)", "GoodWe module discovery"]],
  [2200, 7160]
));
children.push(H2("9.2 Bundled scripts"));
children.push(table(
  ["Script", "Purpose"],
  [
    [[mono("scripts/install-docker.sh")], "Install Docker Engine + Compose (RHEL/Debian)"],
    [[mono("scripts/build.sh")], "Build + start the stack with the commit baked in"],
    [[mono("scripts/update.sh")], "Host updater: git pull + rebuild + restart"],
    [[mono("scripts/probe.py")], "In-container reachability probe"],
    [[mono("cli.py")], "Standalone single-inverter diagnostic/reader"],
  ],
  [3200, 6160]
));
children.push(H2("9.3 Key file locations"));
children.push(bullet([mono("/opt/solar-tracker"), t(" — deployment directory (git checkout)")]));
children.push(bullet([mono(".env"), t(" — site configuration (IPs, tokens, family hints)")]));
children.push(bullet([mono(".update/"), t(" — updater trigger + deployed-version record")]));
children.push(P([new TextRun({ text: "— End of guide —", italics: true, color: GREY })], { alignment: AlignmentType.CENTER, spacing: { before: 400 } }));

// ---------- document ----------
const doc = new Document({
  creator: "Solar Monitor",
  title: "Solar Monitor — Deployment & Operations Guide",
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, color: BLUE, font: "Arial" },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: "2E5A88", font: "Arial" },
        paragraph: { spacing: { before: 220, after: 100 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 23, bold: true, color: "333333", font: "Arial" },
        paragraph: { spacing: { before: 160, after: 80 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      ] },
      { reference: "steps", levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      ] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [ new Paragraph({
      alignment: AlignmentType.RIGHT, border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 4 } },
      children: [new TextRun({ text: "Solar Monitor — Deployment & Operations Guide", size: 16, color: GREY })] }) ] }) },
    footers: { default: new Footer({ children: [ new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      children: [ new TextRun({ text: "dtitsolutions/solar-tracker", size: 16, color: GREY }),
        new TextRun({ text: "\t", size: 16 }),
        new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], size: 16, color: GREY }) ] }) ] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => { fs.writeFileSync("/home/claude/Solar-Monitor-Guide.docx", buf); console.log("written"); });
