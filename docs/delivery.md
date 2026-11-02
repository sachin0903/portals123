# Engineering delivery — how we run Jira & Confluence

This is the project-specific application of the **Engineering Delivery Policy & Procedure v3.0**
("Clear Intent, Fully Auditable") to this repo. The policy itself lives in the team wiki; this doc
translates it into the concrete Epics, ticket conventions, and risk-class rules for the Graphene
platform. Read it alongside [`project-context.md`](project-context.md) (what we're building) and
the root [`CLAUDE.md`](../CLAUDE.md) (standards + gotchas, which double as our global Definition of
Done).

## The one rule: the pickup test

Every ticket must be **pickup-able**: if the person who logged it vanished tomorrow, any competent
engineer could deliver it from the ticket alone — no tribal knowledge, no guesswork. Every field
below is instrumental to that. A field that just restates the title is ceremony; leave it out.

## What this risk profile means for the gate

The policy gates work that is **risk-class first, then size** (>5 days). We are a multi-tenant
financial-services platform where the **app owns authorisation** and **Postgres RLS** enforces
tenant isolation, so a large share of substantive work is risk-class and therefore **gated** (a
quick HoE nod before build). That is the model working as intended for our risk profile — not a
return to the v2.0 turnstile. The thing we protect is the **gate SLA: approval or specific,
actionable feedback within 24–48h**, never an open-ended block.

- **Gated by default:** anything touching auth/identity/secrets, PII/pension/client-money data, DB
  schema/RLS, the partner integrations, or deploy/secret config. See the cheat-sheet below.
- **Self-start (the minority here):** UI copy/styling with no auth or data surface, docs, test-only
  additions, most dependency bumps, non-sensitive chores.

Self-started work is **not** spot-checked to catch people out. The bar is still enforced — by peer
review and the QA that already runs — and addressed directly if a ticket can't be picked up. If a
self-started ticket turns out to be risk-class, it pauses and routes to the gate the moment that's
noticed.

## Where things live

| Artefact             | Role                                                                                                                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Confluence**       | Requirements home. One requirement page per Epic — the business "what & why", decisions, DD trail. **Depth that already lives in the repo `docs/` is linked, not duplicated** (see below). |
| **Epic**             | Single source of truth for a business need. Summarises the requirement and links to Confluence.                                                                                            |
| **Ticket**           | A 1–5 day unit of work, linked to its source. References point to the exact `docs/`/Confluence page needed to start cold.                                                                  |
| **System of record** | The Jira board, owned by the team. Accepted and archived tickets retained with linkage intact. Archiving ≠ deletion.                                                                       |

**Confluence vs the repo `docs/`.** This repo deliberately keeps deep architecture docs in-version
(`docs/auth.md`, `docs/api.md`, `docs/packages.md`, `docs/stack-and-workflows.md`,
`infrastructure/README.md`). Those remain the **canonical technical detail**. Confluence requirement
pages own the business need and **link to the relevant `docs/` page** rather than copying it. The
policy's guarantee — every Epic → Confluence → detail chain is linked and retained — holds either
way; the rule is _don't duplicate, and keep the chain intact._

## Confluence space layout

```
Graphene Platform (space)
├── Platform Overview            — summarises project-context.md
├── Architecture & Decisions     — links the repo docs/ as canonical detail
├── Requirements (one page per Epic — the "what & why" + DD trail)
└── Decision log / ADRs
```

## Epics

Several of these are already built — capture them retroactively for DD-readiness (see the last
section). Risk-class is the routing key for the gate.

| Epic                                                 | Risk-class                            | Status                    |
| ---------------------------------------------------- | ------------------------------------- | ------------------------- |
| Authentication, MFA & sessions                       | Auth / identity                       | Built — capture for audit |
| Three-portal architecture + shared `@graphene/auth`  | Architectural blast radius            | Built                     |
| **Data model & tenant isolation (RLS)**              | DB schema + PII + architectural       | **Next — the priority**   |
| Generated API client / typed contract                | External API contract                 | Built (CI drift-guarded)  |
| CI/CD, secret scanning & Coolify deploy              | Secrets handling + deploy             | In progress               |
| Partner integrations (Morgan Lloyd, SEI, CTC, DCube) | Integrations + client money / pension | Future                    |
| Client / Platform / Admin portal features            | Mostly gated (PII surface)            | Blocked on data model     |

## Ticket field model

Two fields are always present; the rest appear only when they change what a picker would do
(policy §7). **Smallest valid ticket:** `Title + Type + Risk-class + Goal + Detail + Verification +
source link (if one exists)`.

| Field          | When                 | Notes for this repo                                                                                        |
| -------------- | -------------------- | ---------------------------------------------------------------------------------------------------------- |
| Goal / Why     | Always               | Intent in one line. A bug's "why" is its impact.                                                           |
| Detail         | Always               | Bugs: repro + expected vs actual + env. Features: sets the scene.                                          |
| Verification   | Always (form varies) | Feature → acceptance criteria; bug → "repro gone + named regression check"; spike → "decision documented". |
| Risk-class     | Always               | Default `None`. The gate routing key — see cheat-sheet.                                                    |
| Source link    | If one exists        | Epic / Confluence / incident. Independent tech work links its `docs/` page or PR.                          |
| Out of scope   | Conditional          | Net-new features & refactors (bound the blast radius). Omit for bugs/chores.                               |
| NFRs           | Conditional          | Where perf/security/data actually bites — in practice, the risk-class tickets.                             |
| References     | Usually              | The pickup test in practice: the exact `docs/` page, prior decisions, data.                                |
| Per-ticket DoD | Rarely               | Only extras beyond the global DoD (below).                                                                 |

## Global Definition of Done (defined once)

Our global DoD **is** the root [`CLAUDE.md`](../CLAUDE.md) "Standards (every change)" section, plus
these repo specifics. Do **not** restate per ticket — only record additions or deviations.

- `moon run :lint :check :typecheck :test` is green. (Note `:check` is svelte-check on the SvelteKit
  apps and `:typecheck` is mypy on the API — **both** are required; one is not the other.)
- Tests ship with the change; co-located `*.test.ts` (vitest) / `test_*.py` (pytest). A bug fix adds
  a test that failed before it.
- Code peer-reviewed and merged; no regressions.
- Technical QA passed (Senior Engineer; **+ HoE if risk-class** — paired Tech QA).
- Functional QA / acceptance passed where there's a user-facing or behavioural change (PO/Ops).
- Docs a change invalidates are updated in the **same** change — repo `docs/`, `CLAUDE.md`, and the
  Confluence requirement page.
- API contract changed → `@graphene/api-client` regenerated and committed (the CI `client-drift`
  job enforces this).
- Secrets never hit git or logs; only `.env*.example` is tracked.

**Per-ticket DoD additions** (record only when they apply): RLS policy shipped in the same
migration; `CREATE INDEX CONCURRENTLY` + a sane `lock_timeout`; expand/contract migration; feature
flag default-off; partner sign-off; back-population script.

Non-code work (spikes) substitutes a written outcome — decision/recommendation/findings — captured
in Confluence (or `docs/`) and linked from the ticket.

## Risk-class cheat-sheet (path / change → gate)

| Change touches…                                                                              | Risk-class                         | Gate                       |
| -------------------------------------------------------------------------------------------- | ---------------------------------- | -------------------------- |
| `pkgs/auth/**`, `apps/*/src/lib/server/workos.ts`, `hooks.server.ts`, `apps/api/app/auth.py` | Auth / identity / secrets          | **Gated + paired Tech QA** |
| `apps/api/migrations/**`, any new table/schema, RLS policy                                   | DB schema + PII                    | **Gated + paired Tech QA** |
| `apps/api/app/routers/**` request/response shape (OpenAPI)                                   | External API contract              | **Gated**                  |
| future `integrations/**` (Morgan Lloyd, SEI, CTC, DCube)                                     | Integration + client money/pension | **Gated + paired**         |
| `docker-compose.yaml`, `infrastructure/**`, `.github/workflows/**` touching secrets/deploy   | Secrets handling + deploy          | **Gated**                  |
| `apps/*/src/routes/**` UI/copy with no auth or data surface                                  | None                               | Self-start                 |
| `pkgs/ui/**`, docs, dependency bumps, test-only additions                                    | None                               | Self-start                 |

When in doubt, treat it as risk-class and take the 24–48h nod — the cost of a gate is small; the
cost of a bad start in auth or tenant isolation is not.

## Technical QA — scale rigour to risk

- **Routine tickets:** single-reviewer Tech QA — Senior Engineer (or HoE) clears alone.
- **Risk-class / architectural tickets:** paired Tech QA (HoE + Senior Engineer), matching the gate
  triggers above.

A ticket is "done" only when technical QA and (where applicable) functional QA have both passed.

## Worked examples

**Gated — the RLS work that's next:**

```
Title:        [API] Tenant table + RLS policy for <first domain entity>
Type:         Feature      Risk-class: DB schema + PII  (gated, paired Tech QA)
Source:       Epic: Data model & tenant isolation → Confluence requirement
Goal:         First tenant-scoped table so portals can store real data under RLS isolation.
Detail:       Expand/contract migration; table + RLS policy in the SAME migration; app role
              DML-only, migrator role does DDL; CREATE INDEX CONCURRENTLY + lock_timeout.
Verification: Cross-tenant read returns 0 rows in a test; tenant_session wired to the endpoint
              and fails closed (403) without an org.
Out of scope: <related entities deferred to later tickets>
NFRs:         Isolation must hold under connection pooling.
DoD adds:     RLS policy shipped in-migration; rollback verified.
```

**Self-start — routine:**

```
Title:        [client] Fix login page copy
Type:         Chore        Risk-class: None  (self-start)
Goal:         Stale wording on the login page.
Detail:       Update copy in apps/client/src/routes/login/+page.svelte.
Verification: client:check green; visually correct.
```

## Standing rules

- **Ticket size:** keep tickets 1–5 days. Split anything larger; if it genuinely can't be split, it
  routes through the gate as one large ticket.
- **Capacity:** protect 20–30% of capacity for engineer-initiated technical work (this repo carries
  real architecture/tech-debt investment — e.g. the data-model design, RLS, the typed client). Hold
  it under delivery pressure, not only when the roadmap is quiet.
- **Escalate early:** auth, data privacy, schema, integrations, or major refactors — raise with the
  HoE before logging detail.

## DD-readiness — capture what's already built

Auth, the three-portal split, and the generated API client are built but were never logged as
tickets. The backlog is only DD-ready when every accepted change links requirement → code. Create
the corresponding Epics and a thin set of "delivered" tickets that link the merged PRs and the
relevant `docs/` pages, then archive them. Cheap now, and it closes the audit trail before any
investor or compliance review.
