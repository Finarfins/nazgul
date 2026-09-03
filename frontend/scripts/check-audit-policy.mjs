import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
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
const AUDIT_ATTEMPTS = 3;
const AUDIT_BACKOFF_MS = [1_000, 4_000];
const LOCKFILE_KEY = "$lockfile";
const LOCKFILE_NAME = "package-lock.json";
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
// AG SINIFI HATA KODLARI: yalnizca bunlar "kayit defterine ulasilamadi"
// sayilir. Baska her sey -- bozuk yuk, politika ihlali, npm'in kendi
// cokmesi -- KIRMIZI kalir. Liste bilerek dar: genisledikce, bir
// politika ihlalinin ag arizasi kilifina girme ihtimali dogar.
const NETWORK_ERROR_CODES = new Set([
  "ETIMEDOUT",
  "ENOTFOUND",
  "EAI_AGAIN",
  "ECONNRESET",
  "ECONNREFUSED",
  "ENETUNREACH",
  "ENETDOWN",
  "EHOSTUNREACH",
  "EPIPE",
  "ERR_SOCKET_TIMEOUT",
  "ENOTCACHED",
]);
const VULNERABILITY_LEVELS = ["info", "low", "moderate", "high", "critical"];

function unusableAuditPayload(message) {
  return new Error(`AUDIT_PAYLOAD_UNUSABLE: ${message}`);
}

function auditUnavailable(message) {
  const error = new Error(`AUDIT_UNAVAILABLE: ${message}`);
  error.auditUnavailable = true;
  return error;
}

export function isNetworkClassFailure({ error, stderr = "", stdout = "" }) {
  if (error && NETWORK_ERROR_CODES.has(error.code)) {
    return true;
  }
  const haystack = `${stderr}
${stdout}`;
  for (const code of NETWORK_ERROR_CODES) {
    if (haystack.includes(code)) {
      return true;
    }
  }
  return /request to https?:\/\/\S+ failed|network (?:error|timeout)|getaddrinfo|socket hang up/i.test(
    haystack,
  );
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
    if (rawId === LOCKFILE_KEY) {
      continue;
    }
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

// CEVRIMDISI, BELIRLENIMCI SINYAL.
// npm audit'in yasayan cagrisi bir ucuncu tarafin ayakta olmasina baglidir;
// bu kontrol degildir. Kilit dosyasinin sha256'si allowlist icinde beyan
// edilir: bagimlilik agaci degistiginde, kayit defterine hic ulasilamasa
// bile kapi KIRMIZI yanar ve beyanin bilerek guncellenmesini ister.
export function enforceLockfileIntegrity(
  rawPolicy,
  lockfilePath = resolve(FRONTEND_ROOT, LOCKFILE_NAME),
  { read = readFileSync } = {},
) {
  const declared = rawPolicy?.[LOCKFILE_KEY];
  if (
    !declared ||
    typeof declared !== "object" ||
    Array.isArray(declared) ||
    typeof declared.sha256 !== "string" ||
    !SHA256_PATTERN.test(declared.sha256)
  ) {
    throw new Error(
      `AUDIT_POLICY_RED: audit allowlist has no valid "${LOCKFILE_KEY}".sha256 (64 hex chars) for ${LOCKFILE_NAME}`,
    );
  }
  const actual = createHash("sha256").update(read(lockfilePath)).digest("hex");
  if (actual !== declared.sha256) {
    throw new Error(
      `AUDIT_POLICY_RED: ${LOCKFILE_NAME} sha256 mismatch: declared ${declared.sha256}, actual ${actual}. ` +
        `Bagimlilik agaci degisti; allowlist icindeki "${LOCKFILE_KEY}".sha256 degerini bilerek guncelleyin.`,
    );
  }
  return actual;
}

function parseArguments(argv) {
  const options = {
    allowlist: DEFAULT_ALLOWLIST,
    auditJson: null,
    degradeOnUnavailable: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--degrade-on-unavailable") {
      options.degradeOnUnavailable = true;
    } else if (argument === "--allowlist" || argument === "--audit-json") {
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
      // TESHIS SOZCUGU KORUNDU: "AUDIT_TIMEOUT" mevcut bir alt-testin
      // sabitledigi metindir; degisen tek sey, artik ag sinifi olarak
      // isaretlenip yeniden denenebilmesidir.
      throw Object.assign(
        new Error(
          `AUDIT_TIMEOUT: npm audit did not return within ${timeoutMs}ms; no audit policy decision was made`,
        ),
        { auditUnavailable: true },
      );
    }
    if (isNetworkClassFailure(result)) {
      throw auditUnavailable(`npm audit could not reach the registry: ${result.error.message}`);
    }
    throw new Error(`npm audit could not start: ${result.error.message}`);
  }
  // AG SINIFI AYRIMI, CIKIS KODUNDAN ONCE VE YUKTEN ONCE.
  // OLCULDU: npm, kayit defterine ulasamadiginda da CIKIS 1 verir -- yani
  // "acik bulundu" ile ayni kod. Cikis kodunu once eleyen bir sira, gercek
  // bir ag arizasini "bozuk yuk" diye KIRMIZI yakar (bu betikte tam olarak
  // bu kusur vardi: stub'lu prova "npm audit output is not valid JSON"
  // uretti). Bu yuzden karar SIRASI: once yuk okunabilir mi, degilse ag mi.
  let audit = null;
  let parseError = null;
  try {
    audit = parseJson(result.stdout, "npm audit output");
  } catch (error) {
    parseError = error;
  }
  if (parseError || ![0, 1].includes(result.status)) {
    if (isNetworkClassFailure(result)) {
      throw auditUnavailable(
        `npm audit could not reach the registry (exit ${result.status}): ${result.stderr.trim()}`,
      );
    }
    if (parseError) {
      throw parseError;
    }
    throw new Error(
      `npm audit failed with exit ${result.status}: ${result.stderr.trim()}`,
    );
  }
  return audit;
}

function sleepSync(durationMs) {
  if (durationMs > 0) {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, durationMs);
  }
}

// UC DENEME, SONRA BOZULMA -- FAIL-OPEN DEGIL, DAR BIR AGIZ.
// Yalniz AUDIT_UNAVAILABLE (ag sinifi) yeniden denenir. Bir politika ihlali
// ya da bozuk yuk ILK denemede firlar; tekrar denemek onu gizlemez.
export function runNpmAuditWithRetry({
  spawn = spawnSync,
  timeoutMs = AUDIT_TIMEOUT_MS,
  attempts = AUDIT_ATTEMPTS,
  backoffMs = AUDIT_BACKOFF_MS,
  sleep = sleepSync,
  log = console.error,
} = {}) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return runNpmAudit({ spawn, timeoutMs });
    } catch (error) {
      if (!error.auditUnavailable) {
        throw error;
      }
      lastError = error;
      log(`AUDIT_RETRY ${attempt}/${attempts}: ${error.message}`);
      if (attempt < attempts) {
        sleep(backoffMs[attempt - 1] ?? backoffMs.at(-1) ?? 0);
      }
    }
  }
  throw lastError;
}

export function main(argv = process.argv.slice(2), { audit: auditRunner } = {}) {
  const options = parseArguments(argv);
  const rawPolicy = parseJson(
    readFileSync(options.allowlist, "utf8"),
    options.allowlist,
  );
  // KILIT KONTROLU HER ZAMAN VE ONCE: bozulmus gecis (degraded) yolunda bile
  // calisir. Ag olmadan da bir sinyal kalmasinin tek sebebi budur.
  enforceLockfileIntegrity(rawPolicy, resolve(FRONTEND_ROOT, LOCKFILE_NAME));
  const policy = parseAllowlist(rawPolicy);

  let audit;
  if (options.auditJson) {
    audit = parseJson(readFileSync(options.auditJson, "utf8"), options.auditJson);
  } else {
    try {
      audit = (auditRunner ?? runNpmAuditWithRetry)();
    } catch (error) {
      // BOZULMUS GECIS YALNIZ AG SINIFINDA. Bir politika ihlali ya da bozuk
      // yuk buraya hic ulasmaz: onlar auditUnavailable tasimaz ve asagidaki
      // firlatma ile KIRMIZI yanar.
      if (options.degradeOnUnavailable && error.auditUnavailable) {
        console.log(
          `::warning::${error.message}. ` +
            `Kilit dosyasi butunlugu DOGRULANDI; yasayan GHSA kontrolu gunluk (zorunlu olmayan) is akisina birakildi.`,
        );
        return "AUDIT_UNAVAILABLE";
      }
      throw error;
    }
  }

  const advisories = extractAuditAdvisories(audit);
  enforceAuditPolicy(advisories, policy);

  const ids = [...advisories.keys()].sort();
  console.log(
    `AUDIT_POLICY_GREEN: exact GHSA set matched (${ids.length}): ${ids.join(", ")}`,
  );
  for (const id of ids) {
    console.log(`ACCEPTED ${id}: ${policy.get(id).reason}`);
  }
  return "AUDIT_POLICY_GREEN";
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
