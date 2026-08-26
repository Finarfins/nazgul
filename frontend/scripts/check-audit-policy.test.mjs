import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  enforceAuditPolicy,
  extractAuditAdvisories,
  main,
  parseAllowlist,
  runNpmAudit,
} from "./check-audit-policy.mjs";

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
