import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  enforceAuditPolicy,
  enforceLockfileIntegrity,
  extractAuditAdvisories,
  isNetworkClassFailure,
  main,
  parseAllowlist,
  runNpmAudit,
  runNpmAuditWithRetry,
} from "./check-audit-policy.mjs";

const LOCKFILE_SHA256 =
  "0de0d540ff11abdf7f88f31addcf313e45f5db18b592a8235d8933302d1a08d2";

function networkFailureSpawn(code = "ENOTFOUND") {
  return () => ({
    error: Object.assign(new Error(`synthetic ${code}`), { code }),
    stdout: "",
    stderr: "",
    status: null,
  });
}

const ACCEPTED_ID = "GHSA-w5hq-g745-h8pq";

function advisory(id, packageName = "uuid") {
  return {
    source: 1,
    name: packageName,
    dependency: packageName,
    title: `Synthetic ${id}`,
    url: `https://github.com/advisories/${id}`,
    severity: "moderate",
    range: "<99.0.0",
  };
}

function auditWith(...entries) {
  return {
    auditReportVersion: 2,
    vulnerabilities: {
      uuid: {
        name: "uuid",
        severity: "moderate",
        isDirect: false,
        via: entries,
        effects: ["xcode"],
        range: "<99.0.0",
        nodes: ["node_modules/uuid"],
        fixAvailable: false,
      },
    },
    metadata: {
      vulnerabilities: {
        info: 0,
        low: 0,
        moderate: entries.length,
        high: 0,
        critical: 0,
        total: entries.length,
      },
    },
  };
}

function policyWith(entries = {}) {
  return parseAllowlist({
    [ACCEPTED_ID]: {
      package: "uuid",
      reason: "Accepted test fixture",
    },
    ...entries,
  });
}

test("exact allowlisted GHSA set passes", () => {
  const audit = auditWith(advisory(ACCEPTED_ID));
  audit.vulnerabilities.xcode = {
    name: "xcode",
    severity: "moderate",
    isDirect: false,
    via: ["uuid"],
    effects: ["@capacitor/cli"],
    range: "*",
    nodes: ["node_modules/xcode"],
    fixAvailable: false,
  };
  audit.vulnerabilities["@capacitor/cli"] = {
    name: "@capacitor/cli",
    severity: "moderate",
    isDirect: true,
    via: ["xcode"],
    effects: [],
    range: "*",
    nodes: ["node_modules/@capacitor/cli"],
    fixAvailable: false,
  };
  audit.metadata.vulnerabilities.moderate = 3;
  audit.metadata.vulnerabilities.total = 3;
  const actual = extractAuditAdvisories(audit);
  assert.doesNotThrow(() => enforceAuditPolicy(actual, policyWith()));
});

test("an unresolved string via entry fails closed", () => {
  const audit = auditWith(advisory(ACCEPTED_ID));
  audit.vulnerabilities.orphan = {
    name: "orphan",
    severity: "high",
    isDirect: false,
    via: ["missing-advisory-node"],
    effects: [],
    range: "*",
    nodes: ["node_modules/orphan"],
    fixAvailable: false,
  };
  audit.metadata.vulnerabilities.high = 1;
  audit.metadata.vulnerabilities.total = 2;
  assert.throws(
    () => extractAuditAdvisories(audit),
    /AUDIT_PAYLOAD_UNUSABLE: npm audit entry orphan references missing via entry missing-advisory-node/,
  );
});

test("a structurally empty payload without a supported report version is unusable", () => {
  assert.throws(
    () =>
      extractAuditAdvisories({
        vulnerabilities: {},
        metadata: { vulnerabilities: { total: 0 } },
      }),
    /AUDIT_PAYLOAD_UNUSABLE: unsupported or missing auditReportVersion; expected 2/,
  );
});

test("an explicitly supplied empty audit JSON file never falls back to live audit", () => {
  const directory = mkdtempSync(join(tmpdir(), "audit-policy-empty-"));
  const auditPath = join(directory, "audit.json");
  writeFileSync(auditPath, "", "utf8");
  try {
    assert.throws(
      () => main(["--audit-json", auditPath]),
      /audit\.json is not valid JSON/,
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("inconsistent vulnerability metadata is unusable before policy enforcement", () => {
  const audit = auditWith(advisory(ACCEPTED_ID));
  audit.metadata.vulnerabilities.total = 0;
  assert.throws(
    () => extractAuditAdvisories(audit),
    /AUDIT_PAYLOAD_UNUSABLE: metadata vulnerability counts are inconsistent/,
  );
});

test("a string via cycle is unusable", () => {
  const audit = auditWith(advisory(ACCEPTED_ID));
  audit.vulnerabilities = {
    first: { name: "first", via: ["second"] },
    second: { name: "second", via: ["first"] },
  };
  audit.metadata.vulnerabilities = {
    info: 0,
    low: 0,
    moderate: 2,
    high: 0,
    critical: 0,
    total: 2,
  };
  assert.throws(
    () => extractAuditAdvisories(audit),
    /AUDIT_PAYLOAD_UNUSABLE: npm audit via cycle detected: first -> second -> first/,
  );
});

test("a via chain that resolves to no GHSA is unusable", () => {
  const audit = auditWith(advisory(ACCEPTED_ID));
  audit.vulnerabilities = {
    leaf: { name: "leaf", via: [] },
  };
  audit.metadata.vulnerabilities = {
    info: 0,
    low: 0,
    moderate: 1,
    high: 0,
    critical: 0,
    total: 1,
  };
  assert.throws(
    () => extractAuditAdvisories(audit),
    /AUDIT_PAYLOAD_UNUSABLE: npm audit entry leaf resolves to no GHSA advisory/,
  );
});

test("npm audit has its own timeout and diagnostic", () => {
  let observedTimeout = null;
  const timedOutSpawn = (_command, _arguments, options) => {
    observedTimeout = options.timeout;
    return {
      error: Object.assign(new Error("synthetic timeout"), { code: "ETIMEDOUT" }),
      stdout: "",
      stderr: "",
      status: null,
    };
  };
  assert.throws(
    () => runNpmAudit({ spawn: timedOutSpawn, timeoutMs: 17 }),
    /AUDIT_TIMEOUT: npm audit did not return within 17ms; no audit policy decision was made/,
  );
  assert.equal(observedTimeout, 17);
});

test("an advisory outside the allowlist fails", () => {
  const actual = extractAuditAdvisories(
    auditWith(advisory("GHSA-2222-3333-4444", "outside-package")),
  );
  assert.throws(
    () => enforceAuditPolicy(actual, policyWith()),
    /AUDIT_POLICY_RED: unapproved advisory GHSA-2222-3333-4444/,
  );
});

test("an allowlist entry with no real advisory fails as stale", () => {
  const actual = extractAuditAdvisories(auditWith(advisory(ACCEPTED_ID)));
  assert.throws(
    () =>
      enforceAuditPolicy(
        actual,
        policyWith({
          "GHSA-3333-4444-5555": {
            package: "stale-package",
            reason: "Synthetic stale exemption",
          },
        }),
      ),
    /AUDIT_POLICY_RED: stale allowlist entry GHSA-3333-4444-5555/,
  );
});

test("a second GHSA for an already allowlisted package still fails", () => {
  const actual = extractAuditAdvisories(
    auditWith(
      advisory(ACCEPTED_ID),
      advisory("GHSA-4444-5555-6666", "uuid"),
    ),
  );
  assert.throws(
    () => enforceAuditPolicy(actual, policyWith()),
    /AUDIT_POLICY_RED: unapproved advisory GHSA-4444-5555-6666 \(uuid\)/,
  );
});

// --- BOZULMUS GECIS: AG SINIFI HATA -> UYARI, CIKIS 0 ---

test("network-class spawn failures are classified as unavailable, not red", () => {
  assert.equal(
    isNetworkClassFailure({
      error: Object.assign(new Error("x"), { code: "EAI_AGAIN" }),
    }),
    true,
  );
  assert.equal(
    isNetworkClassFailure({ stderr: "request to https://registry.npmjs.org/ failed" }),
    true,
  );
  assert.equal(
    isNetworkClassFailure({ stderr: "npm ERR! some unrelated explosion" }),
    false,
  );
});

test("a network-class failure is retried three times with backoff, then reported unavailable", () => {
  let attempts = 0;
  const slept = [];
  const spawn = (...args) => {
    attempts += 1;
    return networkFailureSpawn("ENOTFOUND")(...args);
  };
  assert.throws(
    () =>
      runNpmAuditWithRetry({
        spawn,
        attempts: 3,
        backoffMs: [1, 2],
        sleep: (ms) => slept.push(ms),
        log: () => {},
      }),
    /AUDIT_UNAVAILABLE/,
  );
  assert.equal(attempts, 3);
  assert.deepEqual(slept, [1, 2]);
});

test("a non-network failure is NOT retried and stays red", () => {
  let attempts = 0;
  const spawn = () => {
    attempts += 1;
    return {
      error: null,
      stdout: "{}",
      stderr: "npm ERR! catastrophic internal fault",
      status: 7,
    };
  };
  assert.throws(
    () => runNpmAuditWithRetry({ spawn, attempts: 3, sleep: () => {}, log: () => {} }),
    /npm audit failed with exit 7/,
  );
  assert.equal(attempts, 1, "a non-network failure must fail on the first attempt");
});

test("--degrade-on-unavailable turns registry unavailability into a warning and exit 0", () => {
  const logged = [];
  const originalLog = console.log;
  console.log = (line) => logged.push(line);
  try {
    const outcome = main(["--degrade-on-unavailable"], {
      audit: () => {
        throw Object.assign(new Error("AUDIT_UNAVAILABLE: synthetic outage"), {
          auditUnavailable: true,
        });
      },
    });
    assert.equal(outcome, "AUDIT_UNAVAILABLE");
  } finally {
    console.log = originalLog;
  }
  assert.match(logged.join(" | "), /::warning::AUDIT_UNAVAILABLE/);
});

test("--degrade-on-unavailable NEVER swallows a real policy violation", () => {
  const directory = mkdtempSync(join(tmpdir(), "audit-policy-violation-"));
  const auditPath = join(directory, "audit.json");
  writeFileSync(
    auditPath,
    JSON.stringify(auditWith(advisory("GHSA-9999-8888-7777", "uuid"))),
    "utf8",
  );
  try {
    assert.throws(
      () => main(["--degrade-on-unavailable", "--audit-json", auditPath]),
      /AUDIT_POLICY_RED: unapproved advisory GHSA-9999-8888-7777/,
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

// --- CEVRIMDISI KILIT SINYALI ---

test("the declared package-lock.json sha256 matches the real lockfile", () => {
  assert.equal(
    enforceLockfileIntegrity({
      $lockfile: { sha256: LOCKFILE_SHA256 },
    }),
    LOCKFILE_SHA256,
  );
});

test("a changed package-lock.json without an updated declaration is red", () => {
  const directory = mkdtempSync(join(tmpdir(), "audit-policy-lock-"));
  const lockPath = join(directory, "package-lock.json");
  writeFileSync(lockPath, '{"name":"mutated-lockfile"}', "utf8");
  try {
    assert.throws(
      () =>
        enforceLockfileIntegrity(
          { $lockfile: { sha256: LOCKFILE_SHA256 } },
          lockPath,
        ),
      /AUDIT_POLICY_RED: package-lock\.json sha256 mismatch/,
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("a missing or malformed lockfile declaration is red, not skipped", () => {
  assert.throws(
    () => enforceLockfileIntegrity({}),
    /AUDIT_POLICY_RED: audit allowlist has no valid "\$lockfile"\.sha256/,
  );
  assert.throws(
    () => enforceLockfileIntegrity({ $lockfile: { sha256: "kisa" } }),
    /AUDIT_POLICY_RED: audit allowlist has no valid "\$lockfile"\.sha256/,
  );
});

test("the reserved $lockfile key is not mistaken for a GHSA allowlist entry", () => {
  const policy = parseAllowlist({
    $lockfile: { sha256: LOCKFILE_SHA256 },
    [ACCEPTED_ID]: { package: "uuid", reason: "Accepted test fixture" },
  });
  assert.equal(policy.size, 1);
  assert.equal(policy.has(ACCEPTED_ID.toUpperCase()), true);
});

test("a registry timeout is also retried as a network-class failure", () => {
  let attempts = 0;
  assert.throws(
    () =>
      runNpmAuditWithRetry({
        spawn: (...args) => {
          attempts += 1;
          return networkFailureSpawn("ETIMEDOUT")(...args);
        },
        attempts: 3,
        backoffMs: [0, 0],
        sleep: () => {},
        log: () => {},
      }),
    /AUDIT_TIMEOUT/,
  );
  assert.equal(attempts, 3);
});

// GERILEME KAPISI: npm, kayit defterine ulasamadiginda da CIKIS 1 verir --
// "acik bulundu" ile ayni kod. Bu test, o durumun "bozuk yuk" diye KIRMIZI
// yanmasini degil, ULASILAMADI olarak siniflanmasini sabitler.
test("a registry outage that exits 1 with empty stdout is unavailable, not a broken payload", () => {
  const outageSpawn = () => ({
    error: null,
    stdout: "",
    stderr: [
      "npm ERR! code ENOTFOUND",
      "npm ERR! request to https://registry.npmjs.org/ failed, reason: getaddrinfo ENOTFOUND",
    ].join(" "),
    status: 1,
  });
  assert.throws(() => runNpmAudit({ spawn: outageSpawn }), (error) => {
    assert.equal(error.auditUnavailable, true);
    assert.match(error.message, /AUDIT_UNAVAILABLE: npm audit could not reach the registry/);
    assert.doesNotMatch(error.message, /not valid JSON/);
    return true;
  });
});
