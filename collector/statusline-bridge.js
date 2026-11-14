"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const STDIN_TIMEOUT_MS = 950;

function parseArgs(argv) {
  const args = { source: "", forward: "", echo: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--source" && argv[i + 1]) {
      args.source = argv[++i];
    } else if (a === "--forward" && argv[i + 1]) {
      args.forward = argv[++i];
    } else if (a === "--echo") {
      args.echo = true;
    }
  }
  return args;
}

function getSnapshotDir() {
  const localAppData =
    process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  return path.join(localAppData, "AIQuotaMonitor", "snapshots");
}

function sanitizePayload(source, data) {
  const now = new Date();
  const base = {
    source,
    captured_at: now.toISOString(),
    captured_at_epoch: Math.floor(now.getTime() / 1000),
    version: typeof data?.version === "string" ? data.version : null,
    product: typeof data?.product === "string" ? data.product : null,
    plan_tier: typeof data?.plan_tier === "string" ? data.plan_tier : null,
    model: data?.model && typeof data.model === "object"
      ? {
          id: data.model.id ?? null,
          display_name: data.model.display_name ?? null,
        }
      : null,
    context_window: data?.context_window && typeof data.context_window === "object"
      ? {
          context_window_size: data.context_window.context_window_size ?? null,
          used_percentage: data.context_window.used_percentage ?? null,
          remaining_percentage: data.context_window.remaining_percentage ?? null,
          total_input_tokens: data.context_window.total_input_tokens ?? null,
          total_output_tokens: data.context_window.total_output_tokens ?? null,
        }
      : null,
  };

  if (source === "agy" && data?.quota && typeof data.quota === "object") {
    base.quota = data.quota;
  }

  if (source === "claude" && data?.rate_limits && typeof data.rate_limits === "object") {
    base.rate_limits = data.rate_limits;
  }

  // Fallback: keep whichever quota/rate_limits object exists regardless of source label.
  if (!base.quota && data?.quota && typeof data.quota === "object") {
    base.quota = data.quota;
  }
  if (!base.rate_limits && data?.rate_limits && typeof data.rate_limits === "object") {
    base.rate_limits = data.rate_limits;
  }

  return base;
}

function writeSnapshotAtomic(source, rawInput) {
  if (!source || !rawInput) return;
  let parsed;
  try {
    parsed = JSON.parse(rawInput.replace(/^\uFEFF/, "").trim());
  } catch {
    return;
  }
  const snapshot = sanitizePayload(source, parsed);
  const dir = getSnapshotDir();
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, `${source}.json`);

  // A statusLine payload without quota data (e.g. a fresh session before the
  // first API response) must not wipe the last known limits.
  let previous = null;
  try {
    previous = JSON.parse(fs.readFileSync(target, "utf8"));
  } catch {
    previous = null;
  }
  for (const key of ["rate_limits", "quota"]) {
    const stampKey = `${key}_captured_at_epoch`;
    if (snapshot[key]) {
      snapshot[stampKey] = snapshot.captured_at_epoch;
    } else if (previous && previous[key] && typeof previous[key] === "object") {
      snapshot[key] = previous[key];
      snapshot[stampKey] = previous[stampKey] ?? previous.captured_at_epoch ?? null;
    }
  }
  const tmp = path.join(dir, `${source}.${process.pid}.${Date.now()}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(snapshot, null, 2), "utf8");
  fs.renameSync(tmp, target);
}

function readStdin(timeoutMs) {
  return new Promise((resolve) => {
    const chunks = [];
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try {
        process.stdin.destroy();
      } catch {
        // ignore
      }
      resolve(Buffer.concat(chunks));
    };
    const timer = setTimeout(finish, timeoutMs);
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", finish);
    process.stdin.on("error", finish);
  });
}

function forwardCommand(forwardTarget, rawBuffer) {
  return new Promise((resolve) => {
    if (!forwardTarget) {
      resolve(0);
      return;
    }

    const resolved = path.resolve(forwardTarget);
    let child;
    if (resolved.toLowerCase().endsWith(".js") && fs.existsSync(resolved)) {
      child = spawn(process.execPath, [resolved], {
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
    } else {
      child = spawn(forwardTarget, [], {
        shell: true,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
    }

    child.stdout.on("data", (d) => process.stdout.write(d));
    child.stderr.on("data", (d) => process.stderr.write(d));
    child.on("error", () => resolve(0));
    child.on("close", (code) => resolve(code ?? 0));

    try {
      child.stdin.write(rawBuffer);
      child.stdin.end();
    } catch {
      resolve(0);
    }
  });
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  const rawBuffer = await readStdin(STDIN_TIMEOUT_MS);
  const rawText = rawBuffer.toString("utf8");

  try {
    writeSnapshotAtomic(args.source, rawText);
  } catch {
    // Never break the host CLI statusline on snapshot write errors.
  }

  if (args.forward) {
    const code = await forwardCommand(args.forward, rawBuffer);
    process.exit(code);
  }
})();
