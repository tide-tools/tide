# MOORLINE Prior Art Research

> Researched 2026-06-25. Covers: prior art, reality-rev validation, adversarial failure modes, novelty assessment.

---

## 1. Prior Art Survey

### 1.1 Docs-as-Code + CI Freshness Gates

**What exists:** Several mature approaches to CI-enforced documentation freshness:

- **dosu.dev freshness scoring** — 0-100 composite score combining git age delta (docs vs. code last-modified timestamps), TTL contracts (per-page `ttl_days` frontmatter), and symbol-level drift (regex extraction of `def`/`class` names verified against source). Gate: median >= 75 over rolling 7-day window, hard floor > 60 for critical pages, 4-point PR delta as immediate signal. Bypass: `bypass-freshness-gate` label + semantic LLM review for pages scoring 35-64. Source: https://dosu.dev/blog/score-documentation-freshness-in-ci

- **Fiberplane Drift** (open source, March 2026) — CLI tool anchoring markdown specs to source code via inline markers (`@./src/auth/provider.ts#AuthConfig@a1b2c3d`). Staleness detection uses **XxHash3 of normalized AST fingerprints** via tree-sitter (normalized = node kinds + token text, no whitespace or position data). Per-symbol tracking via `#Symbol` suffix. No VCS history needed — works from stored signatures vs. current file content. Supported: TypeScript, Python, Rust, Go, Zig, Java. Critical limitation: "nothing stops you from re-linking without updating the spec prose" — purely structural, no semantic enforcement. Source: https://fiberplane.com/blog/drift-documentation-linter/ | https://github.com/fiberplane/drift

- **Treedocs** (Swift CLI, DandyLyons, 2025) — Mirrors the filesystem into a `treedocs.yaml` with a "deterministic structural hash of the scanned tree." Pre-commit hook blocks commits when tree is stale. `sync --non-interactive` reconciles fixable drift. Exclude support via `.treedocs/.treedocs_ignore`. Detects file additions/moves/deletions, NOT content changes. Source: https://news.ycombinator.com/item?id=48646209 | https://github.com/DandyLyons/treedocs

- **DocuMate** — Combines static analysis + GitHub Copilot to detect drift, generate accurate docs, maintain health across TS/JS/Python/Markdown.

**MOORLINE comparison:**
- Shares the CI-gate-for-doc-freshness pattern with all of the above.
- MOORLINE's reality-rev (git-tree SHA over covered paths) is coarser than Fiberplane Drift's per-symbol AST fingerprints (more false positives from cosmetic changes) but finer than Treedocs' structural-only hash (catches content changes, not just file movement).
- Critical differentiator: MOORLINE adds a SUBSTANCE check (delta routes content into canonical sections, not just acknowledging staleness) that none of the above tools have. Fiberplane Drift explicitly cannot enforce this. Treedocs cannot either.
- MOORLINE can borrow: Fiberplane's AST-level fingerprinting approach to reduce false positives in reality-rev (see Section 2).

### 1.2 Architecture Decision Records (ADRs) with Drift Detection

**What exists:**
- ADRs (Nygard format, adr.github.io) — append-only markdown decision logs. Tools: adr-tools (bash), dotnet-adr, ReflectRally.
- **ADR Fitness Functions** (platformtoolsmith.com, dev.to) — automated tests verifying ADRs are still followed in code. ArchUnit (Java), dependency-cruiser (JS), NetArchTest (.NET). Example: "if your ADR says all services must communicate through the message bus, a fitness function can detect direct HTTP calls and fail the build."
- Key insight from "Operationalizing ADRs": "Documentation tells people what the decision was. Operationalization puts that decision in the path of delivery."

**MOORLINE comparison:**
- ADR fitness functions enforce structural/dependency constraints from decisions, not semantic updates to living canon. They answer "is the code following the decision?" not "has canon absorbed what the code now does?"
- MOORLINE is complementary: ADR fitness functions = forward (decision constrains code); MOORLINE reality-rev = backward (code reality must be absorbed by canon).
- MOORLINE can borrow: the vocabulary of "operationalization" — putting canon in the path of delivery rather than making it optional consultation.
- MOORLINE does NOT borrow: ArchUnit/dependency-cruiser structural enforcement (different problem: those enforce code structure, MOORLINE enforces knowledge capture).

### 1.3 BDD / Living Documentation / Specification by Example

**What exists:**
- Cucumber/Gherkin — executable specs in Gherkin read by humans and computers. Feature files double as living documentation. 2025 State of Continuous Testing: 66% BDD adoption among continuous-testing teams.
- Living docs = generated FROM test runs (code-to-doc direction).

**MOORLINE comparison:**
- Direction is opposite: BDD generates docs FROM code execution results. MOORLINE requires human authors to update canon BEFORE code can ship. BDD living docs are always current (auto-generated). MOORLINE canon requires active curation.
- Shared insight: "freshness is structural" — if the tests don't run, the doc isn't living. MOORLINE's equivalent is reality-rev: if covered code changed, canon must have absorbed it.
- MOORLINE can borrow: the "auto-generate a draft of what changed" concept — when reality-rev trips stale, a helper could generate a draft delta quoting the diff in covered paths, reducing the friction of the DEFINE exit.

### 1.4 Literate Programming

**What exists:**
- Knuth's WEB (1984) — single source file with code + prose interspersed. Tangle = extract code. Weave = generate docs. Noweb = language-agnostic successor.
- 2026 revival with AI: "Developers write literate documents as a single source of truth, edit either prose or code, and AI agents update the complementary part automatically." Source: https://byteiota.com/literate-programming-ai-agents-solve-maintenance/

**MOORLINE comparison:**
- Literate programming = co-location (code and docs same file). MOORLINE = structural coupling via manifest (code and canon separate files, but canon must track covered paths). MOORLINE avoids LP's maintenance impracticality for large codebases.
- MOORLINE can borrow: the AI-assisted update pattern — when reality-rev stales, trigger an LLM draft of the canonical section delta, surfacing it to the human for review in DEFINE state.

### 1.5 Database Schema Drift Detection

**What exists:**
- Atlas (atlasgo.io) — inspects live database schema against desired state in migration files. CI fails when they diverge. Source: https://atlasgo.io/monitoring/drift-detection
- Liquibase — similar: "detect and prevent database schema drift."
- Pattern: desired-state document + live-state inspection + diff = drift signal.

**MOORLINE comparison:**
- Strong structural analogy. MOORLINE's reality-rev is the documentation analog of schema drift: "desired state" = canon, "live state" = covered code paths, "drift signal" = reality-rev moved but canon didn't absorb it.
- MOORLINE can borrow: Atlas's "auto-generate migration to close the gap" concept — when reality-rev trips, auto-propose a delta template populated from the diff.

### 1.6 Contract Testing (Pact)

**What exists:**
- Pact — consumer-driven contract testing. Consumer defines expectations, provider verifies. Gate fails if provider violates consumer's contract.

**MOORLINE comparison:**
- Conceptually: canon = the contract, code = the provider. MOORLINE enforces that the provider (code) absorbed into the contract (canon). Pact enforces that the provider doesn't break the contract. Different directions again, but the "contract must be verified on every delivery" pattern is shared.

### 1.7 Policy-as-Code (OPA/Conftest)

**What exists:**
- OPA + Conftest — Rego policies evaluated against structured data (Terraform plans, Helm charts, Kubernetes manifests). CI gates via `--fail`/`--fail-defined` exit codes. Source: https://www.openpolicyagent.org/docs/cicd

**MOORLINE comparison:**
- `tide cannon gate` is conceptually a policy-as-code gate. The policy = "canon has absorbed reality." Could be expressed in Rego and evaluated by Conftest if canon and arc state are serialized as JSON. This would give MOORLINE the entire OPA ecosystem (policy versioning, audit logging, distributed evaluation).
- MOORLINE can borrow: OPA's tri-state evaluation model — OPA policies can return allow/deny/undefined (analogous to MOORLINE's 0/1/2 tri-state), and Conftest treats undefined as deny. Validates MOORLINE's design choice.

### 1.8 Knowledge Base Staleness + Data Lineage

**What exists:**
- LLM knowledge base freshness scoring (atlan.com) — active metadata infrastructure maintaining live lineage between source datasets and derived documents. Freshness timestamps, ownership tracking, lineage breaks.
- Data freshness SLAs (elementary-data.com) — "simplest measure is gap between last update and use time."
- Key finding: "Freshness scoring without active metadata infrastructure is measurement without remediation."

**MOORLINE comparison:**
- MOORLINE has the metadata infrastructure (arc stamps, cannon-rev, reality-rev), not just scoring. This validates the design choice to embed revs in arc frontmatter rather than just running a diff.

---

## 2. The Keystone: reality-rev Validation and Pitfalls

### 2.1 Is the technique known?

**Partially.** The closest prior art:
- Treedocs: filesystem structure hash (files exist/moved) — does NOT catch content changes.
- Fiberplane Drift: per-symbol AST fingerprint — catches content changes but at symbol granularity with tree-sitter dependency.
- MOORLINE reality-rev: git-tree SHA over declared path globs — catches any file content change in covered paths (coarser than Drift, finer than Treedocs).

The specific formulation of **git-tree SHA over a human-declared coverage manifest, compared across arc birth vs. close, with a semantic content test (canonical sections must update)** appears genuinely novel. No prior art was found combining all three elements.

### 2.2 False Positives

**Primary pitfall:** reality-rev trips on ANY content change in covered paths — including:
- Whitespace/formatting changes (refactoring passes)
- Comment-only changes
- Test file changes within covered paths
- Generated file regeneration
- Config file updates within `src/` directories
- Dependency lock file updates
- Version bumps in package manifests

**Magnitude:** In a typical active repo, 30-60% of commits may be cosmetic or non-API-surface changes. If reality-rev trips on all of these, canon-debt accumulates faster than it can be paid, the gate is permanently red, and developers normalize noop responses — defeating the entire system.

**Evidence:** Fiberplane Drift was explicitly built to avoid this: "Reformatting a file won't trigger a false positive" via AST normalization. This was a core design driver, suggesting the false-positive problem is severe enough in practice to justify the tree-sitter dependency.

**Mitigations for MOORLINE:**

1. **Glob exclusions in canon-covers** (critical): Add `canon-covers-exclude:` patterns for generated code, test files, config, lock files. Example: `exclude: ["**/__pycache__/**", "**/*.pyc", "**/test_*.py", "requirements.txt"]`. These paths never trip reality-rev regardless of changes.

2. **AST surface fingerprinting** (recommended): Instead of full git-tree SHA, compute a hash of the PUBLIC API surface of covered paths — function/class/type signatures extracted via regex (stdlib-friendly) or tree-sitter (accurate). Only trip reality-rev when the API surface changes, not when implementation details or comments change. Regex approach for Python: extract lines matching `^(class |def |async def )` from covered files. This eliminates the largest false-positive class with zero dependency cost.

3. **Commit threshold** (already in M2): Configurable N commits since last canon update before tripping stale. Gives small cosmetic changes time to accumulate before requiring canon attention. Risk: delays detection of real drift.

4. **Diff classification** (advanced): Classify covered-path changes as API-surface (function signatures, exports, public interfaces) vs. implementation (bodies, comments, tests). Only API-surface changes trip reality-rev. Requires language-aware diff parsing.

### 2.3 Maintenance Burden of canon-covers Manifest

**Pitfall:** The `canon-covers:` manifest is itself documentation that can rot. If new source directories appear but aren't added to canon-covers, reality-rev silently covers nothing. This is "manifest rot" — the coverage guarantee decays without any signal.

**Evidence:** Treedocs addresses this by mirroring the filesystem automatically. For MOORLINE, the manifest is human-authored.

**Mitigations:**
1. **Auto-discovery warning**: On every gate run, scan the project's top-level source directories and warn if any are absent from canon-covers. Specifically: directories in `src/`, `lib/`, `pkg/` not listed in any canon-covers manifest generate a WARNING (not FAIL) that appears in the session banner.
2. **Manifest coverage floor**: Require canon-covers to account for >= X% of tracked source files (configurable, default 80%). Below floor = gate STALE.

### 2.4 Churn-Heavy Repos

**Pitfall:** In fast-moving codebases, reality-rev may trip on almost every commit, causing the gate to be permanently stale. Combined with the false-positive problem, this creates a situation where developers never see a green gate.

**Mitigation:**
- Combine glob exclusions (2.2.1) + API surface fingerprinting (2.2.2) + commit threshold (M2).
- Set the threshold per project: high-churn repos get N=20 commits or 7 days; stable repos get N=5 commits or 2 days.

### 2.5 Generated Code

**Specific class of false positive.** Generated code (protobuf bindings, GraphQL types, ORM models) changes on every generation run but the canonical description is "this is generated by X from Y" — which doesn't change.

**Mitigation:** Explicit `generated: true` flag in canon-covers entries for generated paths. These paths contribute to structural coverage (canon acknowledges they exist and why) but are excluded from reality-rev computation.

---

## 3. Adversarial Analysis: Where MOORLINE Fails in Practice

### 3.1 Gate Fatigue / Alarm Blindness

**Failure mode:** When the gate trips too frequently (especially from false positives in reality-rev), developers normalize the signal. Usenix Security 2022 study: 99% of SOC alerts were false positives, causing analysts to ignore the signal entirely. Target breach: FireEye raised the alarm 5 times before the breach was exploited — the team had learned to ignore alerts. Trend Micro survey: 51% of SOC teams feel overwhelmed by alert volume.

**MOORLINE susceptibility:** High, specifically for reality-rev. Every cosmetic commit tripping the gate is a false alarm. After N false alarms, DEFINE exits with noop + boilerplate reason become the reflexive response.

**Mitigations:**
- Fix the false positive rate first (Section 2.2 mitigations). Gate fatigue is a symptom of a miscalibrated gate, not a behavior problem.
- Distinguish signal types in the session banner: "STALE: API surface change in 3 covered paths since last canon update" vs. "STALE: 47 commits since last canon update." Different urgency levels.
- Track and display false-positive rate as a metric: if > X% of gate trips in the last 30 days resulted in noop closes, flag for gate recalibration.

### 3.2 Noop Boilerplate Gaming

**Failure mode:** M3's substance check requires "delta adds content not already a substring of a canonical section." A developer under deadline could write a unique, non-empty but meaningless sentence: "Updated implementation details per recent refactoring." This passes the substring test and clears the gate.

**Evidence:** This exact pattern occurs in code review: mandatory review gates that require a comment produce rubber-stamp comments like "LGTM" that satisfy the gate without genuine review. (Source: https://cybermaniacs.com/cm-blog/rubber-stamp-risk-why-human-oversight-can-become-false-confidence)

**Mitigations:**
1. **Symbol-referencing requirement**: The substance check should require that the delta mention at least one symbol name (function/class/type) from the changed covered paths. "Updated `AuthProvider.verify()` signature to accept refresh tokens" is harder to boilerplate. This doesn't require AST parsing — a regex check against the union of API surface symbols from covered paths is sufficient.
2. **Minimum delta length floor**: Require delta to add at least N characters (e.g., 100) to canonical sections. Prevents pure one-liners from clearing the gate.
3. **Journal-vs-canon routing check**: "Updated implementation details" routed to journal (worklog) should not clear the substance check. Only content routed to `## What it is`, `## State & components`, or `## Interfaces` counts. Already in M3 — emphasize enforcement.

### 3.3 Canon-Debt Cascade (Infinite Reconcile Loop)

**Failure mode:** Multiple arcs accumulate noop-debt simultaneously. The precheck escalation (M3, §3) auto-spawns a `reconcile-canon` arc. But the reconcile-canon arc itself may be unable to close without paying debt for ALL prior noops, which requires understanding the context of each original arc (now closed). If debt accrues faster than reconcile arcs can close, the factory enters a permanent debt state.

**Evidence:** Meta-governance research identifies this as "infinite regress in reflexive learning about how to govern" — governing the governance system itself can trigger the same failure modes. "If every mode of governance fails, metagovernance will also fail."

**Mitigations:**
1. **Debt circuit breaker**: If canon-debt exceeds N open entries (e.g., 5), auto-pause ALL new arc openings until debt is below threshold. This makes the debt visible and costly, not just auditable.
2. **Debt age decay**: Noop-debt entries older than T days (e.g., 90) auto-expire with a journal entry noting the expiry, rather than accumulating indefinitely. This prevents a prehistoric debt tombstone from permanently staling the gate.
3. **Debt ownership**: Each noop-debt entry is owned by a named author. The reconcile-canon arc is assigned to that author. Anonymous debt is unassignable and thus never reconciled.

### 3.4 Manifest Rot (Silent Coverage Decay)

**Failure mode:** Over months, new source directories are added to the project but not to canon-covers. The reality-rev gate covers fewer and fewer paths. Eventually canon is "current" according to the gate but describes only 40% of the actual codebase.

**Evidence:** SBOM maintenance shows this exact pattern — Bill of Materials manifests that are accurate at creation become stale as the project evolves, with the staleness itself being invisible.

**Mitigation:** See Section 2.3 mitigations. Auto-discovery warning is the most important.

### 3.5 M6 Meta-Canon Infinite Regress

**Failure mode:** Tide's own CANON.md (M6) must be maintained. The `tide cannon gate --home` gate enforces this. But who writes tide's arc deltas? If tide is developed by autonomous agents and humans, the same fatigue/boilerplate/debt problems apply to the home canon. The home canon is the most critical artifact (it defines how everything else works) and also the most likely to be neglected because tide changes feel like "just infrastructure."

**Mitigation:** Explicitly define the home canon's reconcile responsibility as owned by the human (not delegatable to agents). Make the home canon's DEFINE exit require a human-signed confirmation that cannot be automated (e.g., a GPG-signed commit). This terminates the regress at the human.

### 3.6 Human Gate Becomes Rubber Stamp

**Failure mode:** Under sustained time pressure, DEFINE exits become reflexive noop+boilerplate. The research is clear: "a HITL checkpoint that a tired engineer rubber-stamps at 2 AM does not represent genuine oversight but rather theater." The force mechanism converts neglect into visible canon-debt (good), but if the developer normalizes writing boilerplate debt-reasons, the debt itself becomes theater.

**Partial mitigation MOORLINE already has:** noop-debt is gate-blocking and auditable. The mitigation is NOT preventing the rubber stamp (impossible) but making the rubber stamp costly and visible in aggregate. If a developer has 8 open canon-debt entries, that's a signal the system surfaces and that a code review / team process review should address.

---

## 4. Novelty Assessment

### 4.1 Genuinely Novel (no direct prior art found)

1. **Two-axis oracle: cannon-rev AND reality-rev as a single gate predicate.** Prior art has one or the other. Treedocs tracks structural reality. Fiberplane Drift tracks per-symbol reality. Neither composes a "has canon changed since the code it covers changed" check as a single POSIX-exit gate that blocks ALL work transitions. This composition is novel.

2. **DEFINE lifecycle state with signed noop → gate-blocking canon-debt.** Converting "override" into "visible, payable, gate-blocking debt" rather than a clean bypass is novel in documentation systems. Code quality tools have technical debt tracking (SonarQube debt score), but no doc system found treats a forced delta bypass as a first-class debt artifact that re-stales the gate.

3. **Non-interactive-proof force.** Most gates are bypassable in interactive mode ("trusted human is present, so allow force"). MOORLINE explicitly closes this loophole by making force produce debt regardless of interactivity context. The research on rubber-stamp risk (security context) identifies interactive exemptions as the primary failure mode; MOORLINE's fix is correct.

4. **Tri-state gate where oracle-error = FAIL-LOUD (not SKIP).** Binary gates are the norm. The explicit third state (oracle-error) treated as hard failure prevents a broken oracle from silently disabling enforcement. This specific design choice is under-documented in the field but important.

5. **Atomic + idempotent + CAS + self-linting merge for a markdown knowledge base.** Concurrency-safe idempotent merge with compare-and-swap on the canonical document revision is a database-grade concurrency control applied to a markdown file. No prior art found combining all four properties for documentation artifacts.

### 4.2 Not Novel (known patterns, reinvented)

1. **CI gate for documentation freshness.** Extensively explored: dosu, Fiberplane, DocuMate, Treedocs, dozens of blog posts. MOORLINE's gate is more sophisticated but not categorically different from existing CI doc gates.

2. **Docs-as-code + git hash for staleness.** Treedocs does this (structural hash). Fiberplane does this (AST fingerprint hash). MOORLINE's git-tree SHA is a variant.

3. **Lifecycle states for knowledge artifacts.** Common in content management systems (Draft → Review → Published → Archived). MOORLINE's DEFINE state is novel in its specific exit criterion (substance check), not in having lifecycle states.

4. **Policy-as-code enforcement in CI.** OPA/Conftest. MOORLINE's gate is conceptually a Rego policy.

5. **Schema drift detection.** Atlas, Liquibase — exact structural analog in database world.

---

## 5. Verdict: What to Adopt, What to Fix, What is Novel

### Adopt

- **Fiberplane Drift's AST/API-surface fingerprinting for reality-rev** (CRITICAL): Replace raw git-tree SHA with a hash of the API surface (function/class/type signatures) of covered paths. For stdlib-only tide, use regex extraction of `def`/`class`/`async def` patterns rather than tree-sitter. This eliminates the largest class of false positives in reality-rev without adding dependencies. See also: https://github.com/fiberplane/drift for the XxHash3 + normalized AST approach.

- **dosu.dev's error budget / bypass economics**: Allow occasional bypasses with a documented error budget (e.g., 3 bypasses per 30-day window per project). Freeze bypasses when budget exhausted. This prevents two failure modes simultaneously: (a) zero-tolerance gates that cause fatigue by blocking legitimate emergency bypasses, and (b) unlimited bypasses that let canon rot unchecked.

- **Treedocs' auto-discovery warning**: On every gate run, diff canon-covers manifest against actual source directories. New uncovered directories generate a WARNING surfaced in the session banner, preventing silent manifest rot.

- **ADR fitness functions' vocabulary**: "Operationalization puts that decision in the path of delivery." MOORLINE's design docs and user-facing language should use this framing to communicate why the gate exists.

### Fix

- **Add `canon-covers-exclude:` glob patterns to M2**: Required to prevent false positives from generated code, test files, config files, and lock files. Without this, reality-rev cannot be deployed in most real repos.

- **Harden M3 substance check with symbol-referencing requirement**: Require the canon delta to mention at least one symbol name from the changed covered paths. Prevents boilerplate gaming.

- **Add debt circuit breaker to M3 §3 escalation**: If canon-debt exceeds N open entries, pause new arc openings until below threshold. Without this, the reconcile-canon arc spawn becomes an infinite loop under heavy concurrent development.

- **Make M6 home-canon reconcile human-exclusive**: Explicitly state that tide's own DEFINE exit requires human sign-off (not agent-delegatable). Terminates the meta-canon regress cleanly.

### What is Novel (preserve and articulate)

1. Two-axis oracle (cannon-rev + reality-rev) as unified gate.
2. Signed noop → gate-blocking canon-debt (force-converts-to-debt).
3. Non-interactive-proof force (no interactive exemption).
4. Tri-state gate with oracle-error = FAIL-LOUD.
5. Atomic + idempotent + CAS + self-linting merge for markdown canon.

---

## Sources

- https://dosu.dev/blog/score-documentation-freshness-in-ci
- https://understandingdata.com/posts/doc-drift-detection-ci/
- https://fiberplane.com/blog/drift-documentation-linter/
- https://github.com/fiberplane/drift
- https://news.ycombinator.com/item?id=48646209 (Treedocs Show HN)
- https://github.com/DandyLyons/treedocs
- https://platformtoolsmith.com/blog/operationalizing-adrs-fitness-functions/
- https://dev.to/alexandreamadocastro/stop-architecture-drift-operationalizing-adrs-with-automated-fitness-functions-22oi
- https://adr.github.io/
- https://atlasgo.io/monitoring/drift-detection
- https://www.liquibase.com/blog/database-drift
- https://www.openpolicyagent.org/docs/cicd
- https://byteiota.com/literate-programming-ai-agents-solve-maintenance/
- https://support.smartbear.com/cucumberstudio/docs/bdd/living-doc.html
- https://cybermaniacs.com/cm-blog/rubber-stamp-risk-why-human-oversight-can-become-false-confidence
- https://www.usenix.org/system/files/sec22-alahmadi.pdf (99% false positives in SOC)
- https://atlan.com/know/llm-knowledge-base-staleness/
- https://www.elementary-data.com/post/data-freshness-best-practices-and-key-metrics-to-measure-success
