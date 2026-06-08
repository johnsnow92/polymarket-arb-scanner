export const meta = {
  name: 'codebase-audit',
  description: 'Read-only multi-dimension codebase audit: scout -> fan-out finders -> dedup -> 3-lens adversarial verify -> completeness critic -> ranked report',
  whenToUse: 'Auditing a codebase (or a slice of one) for bugs, security, test gaps, dead code, and consistency. Pass {path, dimensions?, filesPerChunk?, maxRounds?}. Slice-first: point at one directory before going wide.',
  phases: [
    { title: 'Scout', detail: 'one agent enumerates + chunks source files under the path' },
    { title: 'Find', detail: 'one finder per (dimension x file-chunk), in parallel' },
    { title: 'Verify', detail: '3 perspective-diverse lenses judge findings in batches; majority keeps it, errors mark it unverified' },
    { title: 'Critic', detail: 'completeness critic names missed areas; one bounded re-find round' },
    { title: 'Synthesize', detail: 'one agent writes the ranked report to disk' }
  ]
};

// ---------------------------------------------------------------------------
// Dynamic workflow: comprehensive, read-only codebase audit.
// This is the copy-from TEMPLATE for the rest of the workflow library: the
// find -> dedup -> adversarial-verify -> synthesize skeleton is reused by the
// business/research workflows with MCP-backed finders swapped in.
//
// Orchestration scripts cannot touch the filesystem, run shell, or call
// Date.now()/Math.random(). All file/shell work is done by agents; timestamps
// are produced by the synthesis agent via its own `date` call.
// ---------------------------------------------------------------------------

const DIMENSION_LIBRARY = {
  bugs: {
    key: 'bugs',
    title: 'Correctness & logic bugs',
    prompt: 'logic errors, off-by-one, wrong operators, unhandled None/null, race conditions, incorrect error handling, resource leaks, broken control flow, and edge cases that produce wrong results'
  },
  security: {
    key: 'security',
    title: 'Security vulnerabilities',
    prompt: 'injection (SQL/command/template), hardcoded secrets, unsafe deserialization, missing authz/authn, path traversal, SSRF, weak crypto, unsafe subprocess/eval, and unvalidated external input'
  },
  'test-gaps': {
    key: 'test-gaps',
    title: 'Test coverage gaps',
    prompt: 'public functions and critical branches with no test coverage, untested error paths, missing edge-case tests, and assertions that do not actually verify behavior'
  },
  'dead-code': {
    key: 'dead-code',
    title: 'Dead & unreachable code',
    prompt: 'unused functions/variables/imports, unreachable branches, commented-out blocks, duplicate implementations, and stale feature-flag paths'
  },
  consistency: {
    key: 'consistency',
    title: 'Consistency & convention drift',
    prompt: 'naming/style drift from the surrounding code, inconsistent error-handling patterns, divergent API shapes for similar operations, and copy-paste that should be a shared helper'
  }
};

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          title: { type: 'string', description: 'one-line summary of the issue' },
          file: { type: 'string', description: 'absolute or repo-relative path' },
          line: { type: 'number', description: 'best-guess line number, 0 if unknown' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          evidence: { type: 'string', description: 'the specific code/snippet that proves it' },
          rationale: { type: 'string', description: 'why it is a real problem' },
          fix: { type: 'string', description: 'concrete suggested fix' }
        },
        required: ['title', 'file', 'line', 'severity', 'evidence', 'rationale', 'fix']
      }
    }
  },
  required: ['findings']
};

const SCOUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    language: { type: 'string' },
    fileCount: { type: 'number' },
    summary: { type: 'string', description: 'what this code does, in 2-3 sentences' },
    files: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          path: { type: 'string' },
          approxLines: { type: 'number' },
          role: { type: 'string', description: 'short role, e.g. "api client", "entrypoint", "test"' }
        },
        required: ['path', 'approxLines', 'role']
      }
    }
  },
  required: ['language', 'fileCount', 'summary', 'files']
};

// One verifier agent judges a BATCH of findings under one lens (returns a
// verdict per finding id). Batching keeps the verify fan-out to ~lenses x
// batches agents instead of 3-per-finding, which avoids rate-limit storms.
const VERIFY_BATCH = 8;

const BATCH_VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          id: { type: 'number', description: 'the finding id this verdict is for' },
          isReal: { type: 'boolean', description: 'true ONLY if the issue genuinely holds under this lens' },
          confidence: { type: 'number', description: '0..1' },
          correctedSeverity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          reasoning: { type: 'string' }
        },
        required: ['id', 'isReal', 'confidence', 'correctedSeverity', 'reasoning']
      }
    }
  },
  required: ['verdicts']
};

const CRITIC_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    missedAreas: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          area: { type: 'string', description: 'a file, module, or dimension that was under-examined' },
          why: { type: 'string' }
        },
        required: ['area', 'why']
      }
    },
    coverageNotes: { type: 'string' }
  },
  required: ['missedAreas', 'coverageNotes']
};

const REPORT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    reportPath: { type: 'string', description: 'absolute path to the written markdown report' },
    totalConfirmed: { type: 'number' },
    unverifiedCount: { type: 'number', description: 'findings that could not be verified (e.g. API errors)' },
    bySeverity: { type: 'string', description: 'e.g. "2 critical, 5 high, 9 medium"' },
    topIssues: { type: 'array', items: { type: 'string' } },
    oneLineVerdict: { type: 'string' }
  },
  required: ['reportPath', 'totalConfirmed', 'unverifiedCount', 'bySeverity', 'topIssues', 'oneLineVerdict']
};

/**
 * Normalize args into a config object. Tolerates three shapes the runtime can
 * deliver: a bare directory string, a JSON-encoded string (some invocation
 * paths stringify the args object), and a real object.
 */
function readConfig(raw) {
  let input = raw;
  if (typeof raw === 'string') {
    const trimmed = raw.trim();
    if (trimmed.startsWith('{')) {
      try { input = JSON.parse(trimmed); } catch (e) { input = { path: trimmed }; }
    } else {
      input = { path: trimmed };
    }
  }
  input = input || {};
  const path = input.path || input.target || input.dir;
  if (!path) {
    throw new Error('codebase-audit requires args.path — the directory or repo to audit, e.g. {path:"~/Dev/foo/src"}');
  }
  let dims;
  if (!input.dimensions || input.dimensions === 'all') {
    dims = Object.values(DIMENSION_LIBRARY);
  } else {
    const keys = Array.isArray(input.dimensions) ? input.dimensions : String(input.dimensions).split(',').map(s => s.trim());
    dims = keys.map(k => DIMENSION_LIBRARY[k]).filter(Boolean);
    if (!dims.length) dims = Object.values(DIMENSION_LIBRARY);
  }
  return {
    path,
    dimensions: dims,
    filesPerChunk: Number(input.filesPerChunk) > 0 ? Number(input.filesPerChunk) : 12,
    maxRounds: Number(input.maxRounds) > 0 ? Number(input.maxRounds) : 2
  };
}

/** Split an array into chunks of at most `size`. */
function chunk(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

/** Stable key for cross-dimension dedup: same file + same normalized title. */
function findingKey(finding) {
  const normTitle = String(finding.title || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const file = String(finding.file || '').trim();
  return `${file}::${normTitle}`;
}

function dedupe(findings) {
  const seen = new Map();
  for (const f of findings) {
    const key = findingKey(f);
    if (!seen.has(key)) seen.set(key, f);
  }
  return [...seen.values()];
}

function finderPrompt(dim, files, path) {
  const list = files.map(p => `- ${p}`).join('\n');
  return [
    `You are auditing a codebase for ONE dimension only: ${dim.title}.`,
    `Root under audit: ${path}`,
    `Read ONLY these files (use Read/Grep; do not modify anything — this is read-only):`,
    list,
    '',
    `Find concrete instances of: ${dim.prompt}.`,
    'For each finding: give the exact file, best-guess line, a code snippet as evidence, why it is a real problem, and a concrete fix.',
    'Be precise. Do NOT invent issues to pad the list — an empty findings array is a valid, honest answer.',
    'Return ONLY the structured findings object.'
  ].join('\n');
}

const VERIFY_LENSES = [
  { key: 'correctness', ask: 'Does the described problem ACTUALLY hold when you read the real code? Trace the logic. If the reporter misread the code, mark it not real.' },
  { key: 'impact', ask: 'Even if technically present, does it have real impact (can it be triggered / exploited / cause a wrong result in practice)? Dismiss purely theoretical or unreachable issues.' },
  { key: 'reproduce', ask: 'Construct the concrete input or call path that reproduces this. If you cannot construct one from the actual code, mark it not real.' }
];

function batchVerifyPrompt(batch, path, lens) {
  const items = batch.map(f => [
    `### id=${f._id}  [${f.dimension}]  ${f.title}`,
    `file: ${f.file} (line ~${f.line})`,
    `evidence: ${f.evidence}`,
    `rationale: ${f.rationale}`
  ].join('\n')).join('\n\n');
  return [
    'You are an adversarial verifier. DEFAULT stance: skepticism — assume each finding is WRONG until the real code proves it right.',
    `Verification lens: ${lens.ask}`,
    `Repository root: ${path}`,
    'Open the ACTUAL files and judge each finding below against the real code, not the reporter\'s summary.',
    'Return exactly one verdict per finding id. Set isReal=true ONLY if it genuinely holds under this lens; when uncertain, isReal=false.',
    '',
    items,
    '',
    'Return ONLY the structured object: a verdicts array with one entry per finding id above.'
  ].join('\n');
}

function shortName(p) {
  const parts = String(p || '').split('/');
  return parts[parts.length - 1] || '?';
}

// --------------------------------- run ------------------------------------

const cfg = readConfig(args);
log(`codebase-audit: ${cfg.path} | dimensions: ${cfg.dimensions.map(d => d.key).join(', ')} | chunkSize ${cfg.filesPerChunk} | maxRounds ${cfg.maxRounds}`);

phase('Scout');
const scout = await agent([
  `Enumerate the SOURCE files to audit under: ${cfg.path}`,
  'Use Glob/Bash(ls/find). EXCLUDE: tests fixtures, vendored/third-party, generated code, .venv/venv, __pycache__, node_modules, dist/build, lockfiles, and binary/data files (.db, .json data dumps, images).',
  'Include test files ONLY if a "test-gaps" audit makes sense — list them with role "test" so the auditor can cross-reference.',
  'For each file give path (absolute), approxLines (use wc -l), and a short role.',
  'Return ONLY the structured scout object.'
].join('\n'), { phase: 'Scout', schema: SCOUT_SCHEMA });

if (!scout || !scout.files || !scout.files.length) {
  log('Scout found no source files. Nothing to audit.');
  return { error: 'no source files found', path: cfg.path };
}

const sourceFiles = scout.files.map(f => f.path);
const chunks = chunk(sourceFiles, cfg.filesPerChunk);
log(`Scout: ${scout.language}, ${scout.files.length} files -> ${chunks.length} chunk(s). Fan-out: ${cfg.dimensions.length} dimensions x ${chunks.length} = ${cfg.dimensions.length * chunks.length} finders/round.`);

const confirmed = [];
const unverified = []; // findings whose verdicts errored out — surfaced, never silently dropped
const seenKeys = new Set();
let scopeFiles = chunks; // each round can narrow scope based on the critic

for (let round = 1; round <= cfg.maxRounds; round++) {
  phase('Find');
  log(`Find round ${round}/${cfg.maxRounds}`);
  const finderTasks = [];
  for (const dim of cfg.dimensions) {
    scopeFiles.forEach((files, i) => {
      finderTasks.push(() =>
        agent(finderPrompt(dim, files, cfg.path), {
          label: `find:${dim.key}#${i}`,
          phase: 'Find',
          schema: FINDINGS_SCHEMA
        }).then(r => (r && r.findings ? r.findings.map(f => ({ ...f, dimension: dim.key })) : []))
      );
    });
  }
  const found = (await parallel(finderTasks)).filter(Boolean).flat();

  // Dedup across ALL dimensions this round AND against everything already confirmed.
  const fresh = dedupe(found).filter(f => !seenKeys.has(findingKey(f)));
  log(`Round ${round}: ${found.length} raw findings -> ${fresh.length} fresh after dedup.`);
  if (!fresh.length) {
    log('No fresh findings — converged.');
    break;
  }
  fresh.forEach(f => seenKeys.add(findingKey(f)));

  phase('Verify');
  // Each fresh finding gets a stable id; verifiers judge findings in batches,
  // one batch-agent per (lens x batch). Every finding still receives one vote
  // per lens (3 votes), but with far fewer agents than per-finding fan-out.
  fresh.forEach((f, i) => { f._id = i; });
  const verifyBatches = chunk(fresh, VERIFY_BATCH);
  const lensJobs = [];
  for (const lens of VERIFY_LENSES) {
    verifyBatches.forEach((batch, bi) => {
      lensJobs.push(() =>
        agent(batchVerifyPrompt(batch, cfg.path, lens), {
          label: `verify:${lens.key}#${bi}`,
          phase: 'Verify',
          schema: BATCH_VERDICT_SCHEMA
        }).then(res => ({ lens: lens.key, verdicts: (res && res.verdicts) || [] }))
      );
    });
  }
  const lensResults = (await parallel(lensJobs)).filter(Boolean);

  // Tally votes per finding id. `valid` = verdicts actually returned (errored
  // batches contribute none), `real` = how many said isReal.
  const tally = new Map();
  for (const lr of lensResults) {
    for (const v of lr.verdicts) {
      const t = tally.get(v.id) || { real: 0, valid: 0, severities: [] };
      t.valid += 1;
      if (v.isReal) {
        t.real += 1;
        if (v.correctedSeverity) t.severities.push(v.correctedSeverity);
      }
      tally.set(v.id, t);
    }
  }

  const survivors = [];
  let refutedCount = 0;
  let unverifiedThisRound = 0;
  for (const f of fresh) {
    const t = tally.get(f._id) || { real: 0, valid: 0, severities: [] };
    f.realCount = t.real;
    f.validVotes = t.valid;
    delete f._id;
    if (t.valid < 2) {
      // Fewer than 2 lenses returned a verdict — infra failure, NOT a clean bill.
      f.status = 'unverified';
      unverified.push(f);
      unverifiedThisRound += 1;
    } else if (t.real >= 2) {
      f.status = 'confirmed';
      f.severity = t.severities[0] || f.severity;
      survivors.push(f);
    } else {
      f.status = 'refuted';
      refutedCount += 1;
    }
  }
  log(`Round ${round}: ${fresh.length} findings via ${lensResults.length} batch-verifiers -> ${survivors.length} confirmed, ${refutedCount} refuted (dropped), ${unverifiedThisRound} unverified (verdict errors).`);
  confirmed.push(...survivors);

  if (round >= cfg.maxRounds) break;

  phase('Critic');
  const critic = await agent([
    `You are a completeness critic for a read-only audit of ${cfg.path}.`,
    `Dimensions covered: ${cfg.dimensions.map(d => d.key).join(', ')}.`,
    `Files in scope: ${sourceFiles.length}. Confirmed findings so far: ${confirmed.length}.`,
    'Name specific files/modules/dimensions that were under-examined and likely still hide issues. Be concrete (name files from the tree).',
    'If coverage looks genuinely complete, return an empty missedAreas array.',
    'Return ONLY the structured critic object.'
  ].join('\n'), { phase: 'Critic', schema: CRITIC_SCHEMA });

  const missedFiles = (critic && critic.missedAreas || [])
    .map(m => m.area)
    .flatMap(area => sourceFiles.filter(p => p.includes(area) || area.includes(shortName(p))));
  const nextScope = [...new Set(missedFiles)];
  if (!nextScope.length) {
    log('Completeness critic found no under-examined areas — converged.');
    break;
  }
  log(`Critic flagged ${nextScope.length} file(s) for a targeted re-find round.`);
  scopeFiles = chunk(nextScope, cfg.filesPerChunk);
}

if (!confirmed.length) {
  const why = unverified.length
    ? `0 confirmed, but ${unverified.length} finding(s) could not be verified (verdict errors — likely API rate limits). This is NOT a clean result; re-run to verify.`
    : 'No findings survived verification — clean slice, or scope too narrow.';
  log(why);
  return { path: cfg.path, totalConfirmed: 0, unverifiedCount: unverified.length, note: why };
}

phase('Synthesize');
const payload = confirmed
  .sort((a, b) => severityRank(a.severity) - severityRank(b.severity))
  .map(f => ({
    title: f.title, file: f.file, line: f.line, severity: f.severity,
    dimension: f.dimension, evidence: f.evidence, rationale: f.rationale,
    fix: f.fix, votes: f.realCount
  }));
const unverifiedPayload = unverified.map(f => ({
  title: f.title, file: f.file, line: f.line, severity: f.severity,
  dimension: f.dimension, rationale: f.rationale, validVotes: f.validVotes
}));

const report = await agent([
  `Write a ranked codebase-audit report for ${cfg.path}.`,
  `Scope: ${scout.language}, ${scout.files.length} files. ${scout.summary}`,
  'Below are the VERIFIED findings (each survived a 3-lens adversarial vote). Do not add new findings; organize, rank, and explain these.',
  '',
  `CONFIRMED (${payload.length}):`,
  JSON.stringify(payload, null, 2),
  '',
  `UNVERIFIED (${unverifiedPayload.length}) — verdicts errored out, so these are neither confirmed nor refuted; list them in a clearly-labeled "Unverified — re-run to confirm" section, do NOT rank them with confirmed issues:`,
  JSON.stringify(unverifiedPayload, null, 2),
  '',
  'Report structure: Executive summary (verdict + counts by severity, plus the unverified count) -> Confirmed findings grouped by severity, each with file:line, evidence, why it matters, and the fix -> Unverified section -> a short "remediation order" list for confirmed issues only.',
  'Then write the report to disk: create the directory `.codebase-audit/` under the audited root and write `report-<timestamp>.md`, where <timestamp> comes from your own `date +%Y%m%d-%H%M%S` shell call.',
  `Return ONLY the structured report object; set unverifiedCount=${unverifiedPayload.length} and use the absolute reportPath you wrote.`
].join('\n'), { phase: 'Synthesize', schema: REPORT_SCHEMA });

log(`Done. ${report.totalConfirmed} confirmed (${report.bySeverity}), ${report.unverifiedCount} unverified. Report: ${report.reportPath}`);
return report;

function severityRank(s) {
  return { critical: 0, high: 1, medium: 2, low: 3 }[s] != null ? { critical: 0, high: 1, medium: 2, low: 3 }[s] : 4;
}
