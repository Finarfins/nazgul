import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_ROOT = fileURLToPath(new URL("..", import.meta.url));
const DEFAULT_ALLOWLIST = fileURLToPath(
  new URL("../audit-allowlist.json", import.meta.url),
);
const GHSA_PATTERN = /^GHSA-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$/;
const GHSA_URL_PATTERN = /\/advisories\/(GHSA-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4})(?:$|[?#])/i;
const SUPPORTED_AUDIT_REPORT_VERSION = 2;
const AUDIT_TIMEOUT_MS = 30_000;
const VULNERABILITY_LEVELS = ["info", "low", "moderate", "high", "critical"];

function unusableAuditPayload(message) {
  return new Error(`AUDIT_PAYLOAD_UNUSABLE: ${message}`);
}

function parseJson(raw, source) {
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`${source} is not valid JSON: ${error.message}`);
  }
}

export function extractAuditAdvisories(audit) {
  if (!audit || typeof audit !== "object" || Array.isArray(audit)) {
    throw unusableAuditPayload("npm audit payload must be a JSON object");
  }
  if (audit.auditReportVersion !== SUPPORTED_AUDIT_REPORT_VERSION) {
    throw unusableAuditPayload(
      `unsupported or missing auditReportVersion; expected ${SUPPORTED_AUDIT_REPORT_VERSION}`,
    );
  }
  if (!audit.vulnerabilities || typeof audit.vulnerabilities !== "object") {
    throw unusableAuditPayload("npm audit payload has no vulnerabilities object");
  }
  if (
    !audit.metadata ||
    typeof audit.metadata !== "object" ||
    !audit.metadata.vulnerabilities ||
    typeof audit.metadata.vulnerabilities !== "object" ||
    Array.isArray(audit.metadata.vulnerabilities)
  ) {
    throw unusableAuditPayload("npm audit payload has no vulnerability metadata");
  }
  const counts = audit.metadata.vulnerabilities;
  for (const field of [...VULNERABILITY_LEVELS, "total"]) {
    if (!Number.isInteger(counts[field]) || counts[field] < 0) {
      throw unusableAuditPayload(
        `metadata.vulnerabilities.${field} must be a non-negative integer`,
      );
    }
  }
  const severityTotal = VULNERABILITY_LEVELS.reduce(
    (sum, field) => sum + counts[field],
    0,
  );
  if (severityTotal !== counts.total) {
    throw unusableAuditPayload(
      `metadata vulnerability counts are inconsistent: severities=${severityTotal}, total=${counts.total}`,
    );
  }

  const advisories = new Map();
  const directIdsByNode = new Map();
  for (const [nodeName, vulnerability] of Object.entries(audit.vulnerabilities)) {
    if (!vulnerability || !Array.isArray(vulnerability.via)) {
      throw unusableAuditPayload(`npm audit entry ${nodeName} has no via list`);
    }
    const directIds = new Set();
    for (const via of vulnerability.via) {
      if (typeof via === "string") {
        continue;
      }
      if (!via || typeof via !== "object") {
        throw unusableAuditPayload(
          `npm audit entry ${nodeName} has an invalid via value`,
        );
      }
      if (typeof via.url !== "string") {
        throw unusableAuditPayload(
          `npm audit advisory for ${nodeName} has no URL`,
        );
      }
      const match = via.url.match(GHSA_URL_PATTERN);
      if (!match) {
        throw unusableAuditPayload(
          `npm audit advisory for ${nodeName} has no GHSA id: ${via.url}`,
        );
      }
      const id = match[1].toUpperCase();
      directIds.add(id);
      const packageName = via.name || via.dependency || nodeName;
      const previous = advisories.get(id);
      if (previous) {
        previous.packages.add(packageName);
      } else {
        advisories.set(id, {
          packages: new Set([packageName]),
          severity: via.severity || vulnerability.severity || "unknown",
          title: via.title || "untitled npm advisory",
        });
      }
    }
    directIdsByNode.set(nodeName, directIds);
  }

  const resolvedIdsByNode = new Map();
  function resolveNode(nodeName, ancestors = []) {
    const cached = resolvedIdsByNode.get(nodeName);
    if (cached) {
      return cached;
    }
    const vulnerability = audit.vulnerabilities[nodeName];
    if (!vulnerability) {
      const source = ancestors.at(-1) || "unknown";
      throw unusableAuditPayload(
        `npm audit entry ${source} references missing via entry ${nodeName}`,
      );
    }
    if (ancestors.includes(nodeName)) {
      throw unusableAuditPayload(
        `npm audit via cycle detected: ${[...ancestors, nodeName].join(" -> ")}`,
      );
    }

    const resolvedIds = new Set(directIdsByNode.get(nodeName));
    for (const via of vulnerability.via) {
      if (typeof via !== "string") {
        continue;
      }
      for (const id of resolveNode(via, [...ancestors, nodeName])) {
        resolvedIds.add(id);
      }
    }
    if (resolvedIds.size === 0) {
      throw unusableAuditPayload(
        `npm audit entry ${nodeName} resolves to no GHSA advisory`,
      );
    }
    resolvedIdsByNode.set(nodeName, resolvedIds);
    return resolvedIds;
  }

  for (const nodeName of Object.keys(audit.vulnerabilities)) {
    resolveNode(nodeName);
  }

  if (audit.metadata.vulnerabilities.total > 0 && advisories.size === 0) {
    throw unusableAuditPayload(
      "npm audit reported vulnerable packages but yielded no GHSA advisories",
    );
  }
  return advisories;
}

export function parseAllowlist(rawPolicy) {
  if (!rawPolicy || typeof rawPolicy !== "object" || Array.isArray(rawPolicy)) {
    throw new Error("audit allowlist must be a JSON object keyed by GHSA id");
  }
  const policy = new Map();
  for (const [rawId, entry] of Object.entries(rawPolicy)) {
    const id = rawId.toUpperCase();
    if (!GHSA_PATTERN.test(id)) {
      throw new Error(`invalid audit allowlist key: ${rawId}`);
    }
    if (
      !entry ||
      typeof entry !== "object" ||
      typeof entry.package !== "string" ||
      !entry.package.trim() ||
      typeof entry.reason !== "string" ||
      !entry.reason.trim()
    ) {
      throw new Error(
        `audit allowlist entry ${id} requires non-empty package and reason fields`,
      );
    }
    policy.set(id, {
      package: entry.package.trim(),
      reason: entry.reason.trim(),
    });
  }
  return policy;
}

export function enforceAuditPolicy(advisories, policy) {
  const violations = [];
  for (const [id, advisory] of advisories) {
    const accepted = policy.get(id);
    if (!accepted) {
      violations.push(
        `unapproved advisory ${id} (${[...advisory.packages].sort().join(", ")})`,
      );
      continue;
    }
    if (!advisory.packages.has(accepted.package)) {
      violations.push(
        `allowlist package mismatch for ${id}: expected ${accepted.package}, actual ${[
          ...advisory.packages,
        ]
          .sort()
          .join(", ")}`,
      );
    }
  }
  for (const id of policy.keys()) {
    if (!advisories.has(id)) {
      violations.push(`stale allowlist entry ${id} matches no real advisory`);
    }
  }
  if (violations.length > 0) {
    throw new Error(violations.map((line) => `AUDIT_POLICY_RED: ${line}`).join("\n"));
  }
}

function parseArguments(argv) {
  const options = { allowlist: DEFAULT_ALLOWLIST, auditJson: null };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--allowlist" || argument === "--audit-json") {
      const value = argv[index + 1];
      if (!value) {
        throw new Error(`${argument} requires a path`);
      }
      options[argument === "--allowlist" ? "allowlist" : "auditJson"] = resolve(value);
      index += 1;
    } else {
      throw new Error(`unknown argument: ${argument}`);
    }
  }
  return options;
}

export function runNpmAudit({ spawn = spawnSync, timeoutMs = AUDIT_TIMEOUT_MS } = {}) {
  const bundledNpmEntrypoint = resolve(
    dirname(process.execPath),
    "node_modules",
    "npm",
    "bin",
    "npm-cli.js",
  );
  const npmEntrypoint =
    process.env.npm_execpath ||
    (process.platform === "win32" && existsSync(bundledNpmEntrypoint)
      ? bundledNpmEntrypoint
      : null);
  const npmCommand = npmEntrypoint ? process.execPath : "npm";
  const npmArguments = npmEntrypoint
    ? [npmEntrypoint, "audit", "--json"]
    : ["audit", "--json"];
  const result = spawn(npmCommand, npmArguments, {
    cwd: FRONTEND_ROOT,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
    timeout: timeoutMs,
  });
  if (result.error) {
    if (result.error.code === "ETIMEDOUT") {
      throw new Error(
        `AUDIT_TIMEOUT: npm audit did not return within ${timeoutMs}ms; no audit policy decision was made`,
      );
    }
    throw new Error(`npm audit could not start: ${result.error.message}`);
  }
  const audit = parseJson(result.stdout, "npm audit output");
  if (![0, 1].includes(result.status)) {
    throw new Error(
      `npm audit failed with exit ${result.status}: ${result.stderr.trim()}`,
    );
  }
  return audit;
}

export function main(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  const audit = options.auditJson
    ? parseJson(readFileSync(options.auditJson, "utf8"), options.auditJson)
    : runNpmAudit();
  const rawPolicy = readFileSync(options.allowlist, "utf8");
  const policy = parseAllowlist(parseJson(rawPolicy, options.allowlist));
  const advisories = extractAuditAdvisories(audit);
  enforceAuditPolicy(advisories, policy);

  const ids = [...advisories.keys()].sort();
  console.log(
    `AUDIT_POLICY_GREEN: exact GHSA set matched (${ids.length}): ${ids.join(", ")}`,
  );
  for (const id of ids) {
    console.log(`ACCEPTED ${id}: ${policy.get(id).reason}`);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
