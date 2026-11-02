# 4 · Journeys, flows & tasks — authoring, orchestration, the task engine

Portal: **platform + client**. Part of the [functional audit](README.md) — statuses: ✅ working · ⚠️ partial · 🎭 demo/mock · ❌ missing · 💥 broken

---

## FLOW AUTHORING

### Create flow

- **Status**: ✅
- **What it does today**: `POST /flows` creates the header plus an empty draft version 1, guarding duplicate codes per tenant (`/Users/gio/gpl/portals/apps/api/app/routers/flows.py:114-158`). The platform surfaces a sheet with name → auto-slugged code, optional entity/description, then routes straight into the editor (`/Users/gio/gpl/portals/apps/platform/src/routes/flows/+page.svelte:134-226`, `/Users/gio/gpl/portals/apps/platform/src/routes/flows/+page.server.ts`). RLS + `flows:write` gate it.
- **Expected**: As a compliance lead I create a named process so I can author its steps. As an admin I see it listed as a draft until published.
  - Duplicate code → friendly 409, not a 500.
  - New flow lands in the editor with an empty draft.
  - List shows status, version count, published version.
  - `flows:write` required; readers get a read-only editor.
- **Gaps**:
  - `entity` is free text with no consumer anywhere in the engine — dead field.
  - No duplicate/clone-flow action, so a firm cannot fork the seeded onboarding to experiment.

### Visual editor — add/delete steps

- **Status**: ⚠️
- **What it does today**: The canvas is a _computed_ spanning tree; `steps` is the source of truth and `buildGraph` re-lays it out with dagre on every structural change (`/Users/gio/gpl/portals/apps/platform/src/lib/flow-editor/graph.ts:192-269`). Add appends `step_N` from a type palette; delete filters by id with a confirm dialog (`/Users/gio/gpl/portals/apps/platform/src/routes/flows/[id]/+page.svelte:84-103`). The inspector exposes type, title, assignee, scope, required, per-owner (`/Users/gio/gpl/portals/apps/platform/src/lib/flow-editor/StepInspector.svelte:217-299`).
- **Expected**: As an author I add, configure, reorder and remove steps and see the resulting shape. As an author I trust the canvas reflects what will run.
  - Every config key the validator accepts is editable in the inspector.
  - Steps can be reordered without editing JSON.
  - Node positions persist across reloads.
  - Deleting a step referenced by another step/milestone warns first.
- **Gaps**:
  - **`setup_account` has no inspector branch at all** — `platform_id`/`product_id`/`model_id`/`fee_package_id`/`role` are unauthored, yet `platform_id` is publish-required (`definitions.py:92-99`). Any flow with an account-setup step is unpublishable from the UI.
  - `fill_form.mapping`/`mode`, `upload_document.category`/`format_hint`, `generate_document.dataset`, `accept_terms.category`, `*.category` are all unauthored — the CRM write-back mapping only exists in the seed.
  - `position` is force-nulled on every save (`[id]/+page.svelte:74`, `graph.ts:275`), so dragging a node is discarded — the schema supports it (`definitions.py:238-246`).
  - No step reordering: order is array order and add always appends.
  - `milestones` are held in state but have no editor — declarative milestones are unauthorable.

### Step connections / dependencies (`depends_on`)

- **Status**: ❌
- **What it does today**: `depends_on` validates and normalises (`definitions.py:235-273`), is planned (`planning.py:180-192`) and persisted to `flows.case_steps.depends_on` (`orchestrator.py:250-264`) — and is then read by nothing. `derivation.py` derives purely from `required` + `status`, and `recompute`'s step query does not select it (`orchestrator.py:497-520`). The canvas is explicitly non-connectable (`FlowCanvas.svelte:58`) and the inspector has no dependency field.
- **Expected**: As an author I say "AML only after identity" and the client/staff never see a step before its prerequisite clears.
  - `depends_on` editable in the inspector, drawn as a real edge.
  - A step with unmet dependencies is `blocked` and its artifact is not created.
  - Clearing the dependency releases the step on the next recompute.
  - Cycles rejected at save.
- **Gaps**:
  - Everything instantiates at once: the seeded flow declares `review → idv → aml` (`onboarding_seed.py:1027,1040,1049`) but the client is handed all steps simultaneously and a `crm.kyc_checks` pending row is opened before the fact-find is touched.
  - The editor's edges are cosmetic — an author cannot express or even see real ordering.
  - Only the hardcoded cross-case KYC gate provides sequencing, and it is recovered from `crm.account_contacts` roles, not from the definition (`orchestrator.py:523-549`).

### Step visibility / conditions authoring

- **Status**: ✅
- **What it does today**: The inspector authors a bare `{field, operator, value}` or an AND/OR group over a fixed field list, and a conditional step renders as a yes/no decision node that rejoins the flow (`StepInspector.svelte:143-186,510-583`; `graph.ts:133-151,215-232`). Evaluation is pure and type-safe at plan time (`/Users/gio/gpl/portals/apps/api/app/flows/visibility.py`, `planning.py:142-175`); non-matching steps are simply not planned.
- **Expected**: As an author I make a step apply only to offshore/pension/organisation cases and see the branch on the canvas.
  - Conditions evaluate at plan time against applicant/application/account facts.
  - Unknown operators fail open, never 500.
  - The canvas shows the branch and the summary text.
  - Condition changes never retro-apply to running journeys.
- **Gaps**:
  - Only 8 legacy fields are offered (`graph.ts:112-121`); the docs' example (`service_type = advised`, i.e. contact attributes) is not expressible — no `crm.contacts`/`data` fields in the context.
  - `value` is always captured as a string, so `application.has_pension_product equals true` authored in the UI compares `"true"` to a boolean and silently never matches (the seed passes real booleans via Python).
  - Visibility is evaluated **only** at instantiate; a step wrongly excluded cannot be added later (no re-plan).

### Intake config

- **Status**: ⚠️
- **What it does today**: `_validate_intake` normalises `subject` (accounts|person), the three `collect_*` toggles, `allow_new`, and custom fields (`definitions.py:295-345`), and `FlowOut.intake` publishes the block for the wizard (`flows.py:83-91`). The inspector (opened by clicking the Entry node) edits the toggles and the custom-field list (`/Users/gio/gpl/portals/apps/platform/src/lib/flow-editor/IntakeInspector.svelte:32-68`).
- **Expected**: As an author I declare what starting a journey collects, and the "New journey" screen is generated from it.
  - Every intake key is editable, including `subject`.
  - Custom field values reach the execution and are usable downstream.
  - Required fields block submission.
- **Gaps**:
  - `subject` is not editable anywhere in the UI — the person-only journey is unreachable by authoring.
  - Custom intake answers are stored in `flows.executions.data.intake` (`applications.py:290`) and then **never read** — not by visibility, not by mapping, not displayed on journey detail. Write-only data.
  - `collect_platform: false` still requires a platform client-side (`journeys/new/+page.svelte:86-94`) and server-side (`platform_id: int`).

### Raise-a-task specs

- **Status**: ⚠️
- **What it does today**: A per-step "Raise a task" section adds specs with trigger, assignee kind (team/person/client), title, category, priority, due-days (`StepInspector.svelte:383-508`), validated strictly (`definitions.py:111-165`) and preflighted against real roles/active staff at publish (`flows.py:401-436`).
- **Expected**: As an author I attach "on failure raise a compliance review" to a check and see a badge on the node.
  - Trigger maps 1:1 to engine signals.
  - Role assignment travels by slug between tenants.
  - Publish refuses specs pointing at non-existent roles/users.
  - Placeholders render in the created task.
- **Gaps**:
  - `description` is validated and dispatched but has no input in the inspector.
  - "a person" assignment is a raw `user_id` text box, not a staff picker (`StepInspector.svelte:453-463`).
  - No preview/test: an author cannot tell whether a spec ever fires (and with the v2 flag off it never does).

### Save draft

- **Status**: ✅
- **What it does today**: `POST /flows/{uid}/versions` validates then either overwrites the latest version if it is still a draft, or forks version N+1 from a published one (`flows.py:261-322`). The editor keeps a dirty flag, a `beforeNavigate` unsaved-changes guard and a status line (`[id]/+page.svelte:105-142,119-128`).
- **Expected**: As an author I save work in progress without affecting running journeys, and I am warned before losing edits.
  - 422 validation messages surface inline.
  - Saving after publish forks a new draft.
  - Navigation away with unsaved changes confirms.
- **Gaps**:
  - No optimistic concurrency: two authors on the same draft silently overwrite each other (whole-definition PUT semantics).
  - Draft is a single mutable row — no "discard draft / revert to published".

### Publish (validation + preflight)

- **Status**: ⚠️
- **What it does today**: Publish saves then calls `publish_issues` (≥1 step, required config present) plus `_reference_issues` (form published, document/platform/product/model/fee-package exist, account role valid, task-spec roles/users real), archives the prior published version and repoints the flow (`flows.py:440-497`, `definitions.py:168-185`).
- **Expected**: As a compliance lead I cannot publish a flow that would break at a client's runtime, and I get a list of exactly what to fix.
  - Every step's referenced entity is checked and published.
  - Handler-owned preflight (e.g. mapping validity) runs.
  - Issues are listed per step, actionable in the UI.
  - Publishing is atomic and versions are snapshotted.
- **Gaps**:
  - **`StepHandler.preflight` is dead code** — never called from the publish path (only from `tests/test_flows_handlers.py`). So `fill_form.mapping` is never validated at publish; a bad mapping surfaces at runtime as a `BLOCKED` step (`step_handlers.py:257-268`).
  - The **seeded onboarding flow cannot be re-published**: the seed writes an unvalidated published definition directly (`onboarding_seed.py:1122-1148`) whose `setup_account` config is `{}` (`:1092`), which `publish_issues` rejects and the UI cannot fix.
  - All issues are concatenated into one 422 `detail` string and rendered as a single alert — no per-step highlighting.
  - No pre-publish diff or impact statement (how many in-flight executions, what changes).

### Version history / rollback

- **Status**: ❌
- **What it does today**: `GET /flows/{uid}/versions` and `/versions/{vid}` exist (`flows.py:218-258`), and the editor fetches the list only to seed the builder with `versions[0]` (`[id]/+page.server.ts:58`). There is no history panel, no per-version view, no rollback endpoint or action.
- **Expected**: As a compliance lead I see who published which version when, view an old definition and roll back.
  - Version list with author, timestamp, status.
  - Read-only view of any version.
  - "Restore as draft" from an archived version.
  - Rollback never mutates running executions.
- **Gaps**:
  - No UI at all; the API has no rollback/restore verb (re-publishing an archived version uid would work by accident but nothing calls it).
  - `created_by` is stored but never surfaced.

### Archive flow

- **Status**: ❌
- **What it does today**: `flows.flows.status` allows `archived` and `flowStatusTone` renders it, but `PATCH /flows/{uid}` only updates name/description/entity (`flows.py:174-197`) — nothing can set the status. No delete either.
- **Expected**: As an admin I retire a flow so it can no longer start journeys, without touching journeys already running.
  - Archive hides the flow from the "New journey" picker.
  - Running executions continue on their pinned version.
  - Archived flows are still viewable/unarchivable.
- **Gaps**:
  - Entirely absent; a mis-created flow is permanent clutter.
  - The wizard filters on `status === 'published'` (`journeys/new/+page.server.ts:21`), so archiving _would_ be the right lever if it existed.

## STEP TYPES

### fill_form

- **Status**: ⚠️
- **What it does today**: `instantiate` creates a `complete_form` request item on the participant's per-execution `documents.requests` (`step_handlers.py:245-255`, `orchestrator.py:38-62,177-180`). On submission `on_item_fulfilled` loads the answers and routes through `handle`, which applies the mapping and patches whitelisted `crm.contacts` columns / `data` paths (`orchestrator.py:65-107`, `step_handlers.py:257-306`). The client renders the form via `/documents/requests/.../form` (`/Users/gio/gpl/portals/apps/client/src/routes/onboarding/+page.server.ts:105-136`).
- **Expected**: As a client I complete a form in the portal and my journey advances. As an adviser I complete a staff-assigned fact-find myself.
  - Client-assigned forms appear in the portal and advance the case.
  - Staff-assigned forms have a staff fill surface.
  - Answers are visible on journey detail for review.
  - The form definition is frozen at instantiate.
- **Gaps**:
  - **Staff-assigned `fill_form` has no surface**: `isStaffActionable` excludes it (`/Users/gio/gpl/portals/apps/platform/src/lib/applications.ts:118-124`), `/applications/me` filters `assignee='client'` (`applications.py:379`), and the platform has no document-requests UI. Six of the seeded flow's steps (core/org/offshore/pension/employer/activity) are therefore uncompletable — the seeded journey cannot progress past the fact-find.
  - **No form snapshot in v2**: `flows.case_steps.data` exists for it (`migrations/sql/0023_flows_hardening.sql:13`) but nothing writes it, and `/applications/me` returns no `form`, so the client always renders the _live_ form — the documented protection against mid-flight form edits is lost.
  - Submitted answers are not returned by `GET /applications/{uid}` (steps carry only `request_item_id`), so staff cannot review what the client entered.
  - Application-scope `fill_form` has no contact → no request → permanently `pending` with no actor (`step_handlers.py:246-247`).

### accept_terms

- **Status**: ⚠️
- **What it does today**: `instantiate` creates an `acknowledge` item against the target document (`step_handlers.py:314-336`); the client reads inline terms HTML or a presigned PDF then ticks and confirms, which fulfils the item and trips the orchestrator (`client/src/routes/onboarding/+page.svelte:135-196,439-522`). Preflight requires `document_uid`.
- **Expected**: As a client I read the declaration then agree, and the acceptance is auditable.
  - Document must be visible before the checkbox enables.
  - Each owner of a joint account acknowledges separately.
  - Acceptance timestamp and document are recorded against the step.
- **Gaps**:
  - **Account-scope `accept_terms` without `per_participant` silently dies**: the case subject is an account, so `target_contact` is None, no request is created, `instantiate` returns None and the step sits `pending` with no artifact and no actor (`orchestrator.py:172-180`, `step_handlers.py:324-325`). The account case can never complete.
  - Signature is still "checkbox now, signature later" — no e-signature path despite the `sign` action existing in the requests engine.
  - The linked document/acceptance is not shown on journey detail.

### upload_document

- **Status**: ⚠️
- **What it does today**: Creates an `upload` item with optional category/format hint (`step_handlers.py:339-354`); the client uploads through a dropzone to the per-item fulfil endpoint (`client onboarding +page.svelte:102-133`).
- **Expected**: As a client I upload the requested proof and the step clears. As staff I see the uploaded file.
  - Format hint and category authored and enforced.
  - Uploaded file is linked to the step and viewable by staff.
  - Rejection/re-request possible.
- **Gaps**:
  - `category`/`format_hint` are not authorable in the inspector, so hints never reach the client.
  - No `preflight` and no required config — an upload step with no description is publishable.
  - Same dead-end as `accept_terms` at account/application scope (no contact ⇒ no request).
  - Staff cannot see or reject the uploaded document from the journey.

### setup_account

- **Status**: 🎭
- **What it does today**: `handle` ignores its config entirely and just stamps `crm.applications.synced = true, synced_on = now()` (`step_handlers.py:379-394`) — the "provider write-back seam" is a stub. Staff trigger it from the journey's Complete button (`isStaffActionable` includes it). The accounts themselves were already created by `POST /applications` as `platform.accounts` rows with `status='draft'` (`applications.py:244-273`).
- **Expected**: As an adviser I open the account with the platform and see the real provider reference.
  - The account is created at the provider and its reference stored.
  - `platform.accounts.status` moves off `draft`.
  - Failure surfaces as `sync_failed` with a retry.
- **Gaps**:
  - No provider integration (Seccl/SEI) — clicking "Complete" fakes success.
  - `platform.accounts.status` is never advanced from `draft`; only `crm.applications.synced` moves.
  - The step's whole config (`platform_id`, `product_id`, `model_id`, `fee_package_id`, `role`) is validated at publish but never _used_ by the handler.
  - No failure path: nothing can set the execution `failed`, so the `sync_failed` projection is unreachable.

### verify_identity

- **Status**: 🎭
- **What it does today**: `instantiate` short-circuits to COMPLETED if the contact holds a valid passed check, else inserts a `pending` `crm.kyc_checks` row and returns WAITING (`step_handlers.py:147-178`). Staff record Pass/Refer from journey detail behind a confirm dialog (`journeys/[uid]/+page.svelte` step list; `applications.py:564-593`), which stamps `verified_at`/`valid_until` (`step_handlers.py:191-221`).
- **Expected**: As a compliance officer I run an IDV check and the outcome gates the account. As a client I complete IDV in the portal.
  - Provider check kicked off automatically when configured.
  - Result arrives by webhook and advances the step.
  - A valid prior pass is reused, never duplicated.
  - Referral holds the case with a visible reason.
- **Gaps**:
  - Manual-only: `providers.py` ships a single `ManualProvider` no-op and `get_provider` is never called from any handler — iDenfy is not wired, and there is no webhook route at all.
  - The seed assigns `idv` to the **client** (`onboarding_seed.py:1042`), so the client portal renders a "Complete" button for it that calls `startStep` and does nothing (`client onboarding +page.svelte:241-245`) — a dead button on the most sensitive step.
  - `_record_result` updates "the latest pending check for this (tenant, contact, kind)" rather than the check this step opened — two concurrent journeys for the same person cross-write.
  - `pick_valid`/`is_valid`/`compute_valid_until` in `flows/kyc.py` are unit-tested but unused; the handler re-implements the dedup query inline.

### aml_check

- **Status**: 🎭
- **What it does today**: The same `ReviewHandler` class registered under `aml_check` (`step_handlers.py:407`), with `provider`/`validity_days` config; staff record the outcome.
- **Expected**: As a compliance officer AML screening runs against a provider and ongoing monitoring alerts reach me.
  - Screening kicked off automatically, result by webhook.
  - Adverse media/PEP hits recorded in `raw`.
  - Monitoring alerts re-gate or reopen the application.
- **Gaps**:
  - Creditsafe not wired; `monitoring_ref` column (`migrations/sql/0026_kyc_checks.sql:25`) is never written.
  - `reopen_action` (`flows/kyc.py`) is pure, tested and **called by nothing** — the monitoring/reopen path in 0026 is unimplemented.
  - `validity_days` default is 365 for AML (`flows/kyc.py`), the same as identity, despite the comment saying AML should be shorter.

### generate_document

- **Status**: 💥
- **What it does today**: `instantiate` returns `WAITING` with the comment "Kicked off async; the render worker reports completion" — and does nothing else: no `documents.documents` row, no `documents.render_jobs` insert, no template resolution (`step_handlers.py:360-372`). Nothing anywhere calls back for it.
- **Expected**: As an adviser a suitability pack renders automatically at the right point and lands on the client's record.
  - A document row and render job are enqueued at instantiate.
  - The worker's completion advances the step.
  - Failure fails the step with the render error.
  - The generated PDF is linked to the step/case.
- **Gaps**:
  - **The render is never enqueued** — the enqueue idiom exists two files away (`app/routers/documents.py:432`) but is not used.
  - The step is a required `WAITING` step, so `derive_case_state` pins the case to `WAITING` (`derivation.py`) and the execution can never complete: **any flow containing a `generate_document` step is permanently stuck**, and there is no staff surface to force it (`isStaffActionable` excludes it).
  - `dataset` config is validated but unauthorable and unused.

### review

- **Status**: ✅
- **What it does today**: `handle` maps a non-`passed` outcome to FAILED, and on `passed` sets a whitelisted application gate column (`offshore_approved` + `_on`) before COMPLETED (`step_handlers.py:105-130`). Staff action it from journey detail; the gate feeds `project_onboarding_status` (`projection.py`, `orchestrator.py:600-648`).
- **Expected**: As a compliance officer I approve offshore EDD and the journey moves to account setup.
  - Pass raises the gate and progresses the projection.
  - Fail/refer holds the case visibly.
  - The decision, decider and rationale are auditable.
- **Gaps**:
  - Only one gate is whitelisted (`offshore_approved`) — no way to add a firm-specific gate without code.
  - No rationale/notes captured: `ActionBody` carries only `outcome` (`applications.py:557-562`), so a compliance decision has no recorded reason and no decider on the step.
  - A FAILED review blocks the case with no remediation/re-open action — the only route back is a second `action` call with `passed`.

### task (step type)

- **Status**: ⚠️
- **What it does today**: `instantiate` returns None (step stays `pending`) and `BaseHandler.handle` maps any submission to COMPLETED (`step_handlers.py:91-97`, `handlers.py:112-131`). Staff complete it from journey detail. Config is `description` only (`definitions.py:81`).
- **Expected**: As an adviser a manual to-do inside the journey is assignable, visible to whoever owns it and completable with a note.
  - Staff-assigned tasks appear in the staff work queue.
  - Client-assigned tasks appear as client action items.
  - Completion records who and when.
- **Gaps**:
  - A **client-assigned** `task` step renders a dead "Complete" button in the portal (`startStep` has no branch for it).
  - It is not integrated with `tasks.tasks`: a blocking `task` step is invisible to the Tasks inbox — two unconnected notions of "task" (deliberate per the docs, but the staff has no queue for the step variant).
  - `TaskHandler._config_keys` declares `title`/`assignee` but `definitions.STEP_TYPES["task"]` only allows `description` — saving a title into config is rejected. Registry drift.
  - No assignee-to-person: `assignee` is only `client|staff`.

## JOURNEY EXECUTION

### "New journey" wizard

- **Status**: ⚠️
- **What it does today**: Intake-driven form (flow picker → platform/custody/product toggles → custom fields), accounts with owners, each owner either an existing client or a new name+email created inline as `crm.contacts` tagged `created_via=intake` (`journeys/new/+page.svelte`, `applications.py:104-152`). One POST composes application + accounts + `crm.account_contacts` + starts the execution (`applications.py:182-292`), then redirects to the journey.
- **Expected**: As an adviser I start onboarding for one or two people and their accounts in one screen, creating new clients inline.
  - Only published flows are offered; the form is generated from the flow's intake.
  - New people are created once even if listed on two accounts.
  - Submit is blocked with a reason until valid.
  - Journey opens on success.
- **Gaps**:
  - Everyone is forced to role `owner` (`applications.py:265-273`) — `decision_maker`, `trustee`, `beneficiary` and child/organisation participants cannot be expressed, so the whole trustee/beneficiary half of the KYC-relevance model is unreachable from the UI.
  - No transfer capture, though `has_transfer` drives the Transfer Declaration branch (`applications.py:56`, `planning.py:73`).
  - `adviser_declaration` is hardcoded `true` (`applications.py:234,290`) — the adviser declaration gate is auto-satisfied, so `draft` is effectively unreachable.
  - Intake custom answers are captured and then never used or displayed.
  - **A client already on an unfinished journey of the same flow cannot start another**: `cases_one_active_per_subject` (`migrations/sql/0024_flows_p0_hardening.sql:31`) is a bare unique index and `_persist_case` has no `ON CONFLICT` (`orchestrator.py:202-242`), so the second `POST /applications` raises an integrity error → 500. Since nothing can cancel a journey, the block is permanent.

### Person-only subject (nullable application)

- **Status**: ❌ — **confirmed as suspected**
- **What it does today**: `intake.subject` normalises to `accounts|person` (`definitions.py:338`) and nothing else references it. `POST /applications` rejects an empty `accounts` list (`applications.py:194-195`), `platform_id` is a required `int`, and `crm.applications.platform_id` is `NOT NULL` (`migrations/sql/0002_platform_crm.sql:210`). `start_execution` requires `application_id` and the projection returns early without one (`orchestrator.py:115-127,620`).
- **Expected**: As an adviser I run a person-only journey (annual review, standalone KYC) with no account or platform.
  - Intake `subject: person` yields a people-only wizard.
  - The execution runs with no `crm_application_id`.
  - The journeys board shows it without account columns.
  - The projection degrades to the generic engine state.
- **Gaps**:
  - Needs `flows.executions.crm_application_id` nullable in practice plus a person-only start path — today it is application-shaped end to end.
  - `_graph` builds participants exclusively from `crm.account_contacts` (`applications.py:45-101`), so a subject with no account yields an empty graph and an execution with zero cases (which then derives `draft` forever).
  - The projection is hardwired to the onboarding label set; a non-onboarding flow has no status vocabulary of its own.

### start_execution planning (fan-out + KYC gate)

- **Status**: ⚠️
- **What it does today**: `plan_execution` fans participant steps per person (KYC steps only for individuals in owner/DM/trustee roles), account steps per account with `per_participant` fanning to owners, and one application case, each step filtered by visibility (`planning.py:196-270`). `start_execution` persists cases/steps, creates one `documents.requests` per contact, and calls each handler's `instantiate` (`orchestrator.py:115-199`). `recompute` derives profile cases first, then account cases gated on their owners' completed profiles, then the execution rollup and projection, under `pg_advisory_xact_lock` (`orchestrator.py:446-494`).
- **Expected**: As the engine I fan a definition across the application graph correctly and hold accounts until KYC clears.
  - Joint accounts fan per-owner steps to each owner exactly once.
  - Children/organisations gathered but not KYC-gated.
  - Account cases blocked until owners' profiles complete.
  - Re-plans are idempotent and never lose the rollup.
- **Gaps**:
  - **Gate deadlock**: an account case always gates on its individual owners (`orchestrator.py:523-549`, hardcoded roles), but a profile case only exists if at least one participant step applies (`planning.py:215`). A flow with only account-scoped steps — or a participant whose steps are all conditioned out — leaves `completed_profiles` empty, so the account case is `blocked` forever.
  - **Empty account cases**: account cases are created unconditionally even with zero steps (`planning.py:248-256`); `derive_case_state` returns `DRAFT` for a step-less case, so the execution can never reach `completed` (`derivation.py`).
  - The authored `gate_on_contacts` is discarded on persist and re-derived from CRM roles, so gating is onboarding-specific despite the "generic engine" claim.
  - `sort_order` on `case_steps` is never set — step display order is `id` order, i.e. plan order.
  - No re-plan/repair verb: a mis-started journey must be abandoned (and cannot be).

### Journeys board / list + status projection

- **Status**: ⚠️
- **What it does today**: `GET /applications` returns per-execution rollups (client names, counts, steps done/total, projected application status) (`applications.py:394-432`); the board groups the seven projected labels into five columns with "whose ball" chips and a next-step hint (`lib/applications.ts:195-280`, `journeys/+page.svelte`). With the flag off the list 404s and the page shows an "engine not enabled" empty state (`journeys/+page.svelte:117-127`).
- **Expected**: As an adviser I scan every journey, see who owes what and open the one that needs me.
  - Column placement matches the true engine state.
  - Overdue/blocked journeys stand out.
  - Filtering/searching by client or status.
  - Counts are accurate.
- **Gaps**:
  - The board keys off the **projected onboarding label**, not the engine state, so a `blocked` execution (failed AML, closed gate) is displayed as "In review"/"You" with no distinct blocked treatment.
  - No filter, search, sort or pagination — `list_applications` returns every execution the tenant ever started, unbounded.
  - `stages()` marks _every_ stage done for a cancelled/`sync_failed` journey (`lib/applications.ts:167-171`), rendering a failure as fully complete.
  - `steps_total` counts skipped/cancelled out but `steps_done` counts fulfilled including optional ones — progress can read oddly with optional steps.

### Journey detail + staff actions

- **Status**: ⚠️
- **What it does today**: `GET /applications/{uid}` returns the execution, tiered cases with subject names/roles, their steps and the last 50 `flows.domain_events` (`applications.py:435-554`). The page renders a stage stepper, per-case step checklists with status icons, a blocked-gate explainer, the journey's tasks panel and an activity timeline; staff action `verify_identity`/`aml_check` (Pass/Refer with confirm), `task` and `setup_account` (`journeys/[uid]/+page.svelte`; `applications.py:564-593`).
- **Expected**: As an adviser I see exactly what is holding a journey up and action everything that is mine.
  - Every staff-assigned step is actionable from here.
  - Client-submitted answers/documents are reviewable.
  - Participants can be added/removed/re-roled.
  - Journey can be cancelled, chased or reassigned.
- **Gaps**:
  - Only 4 of 9 step types are staff-actionable; staff-assigned `fill_form`, `review`(!), `upload_document`, `accept_terms` and `generate_document` have no action. `review` is missing from `MANUAL_TYPES` (`lib/applications.ts:117`) — **the offshore compliance review in the seeded flow cannot be actioned**, so every offshore journey stalls.
  - No participant handling: cannot add a trustee, remove a wrongly-added owner, or change a role after start.
  - No "invite client" / resend-activation action — the docs' `POST /cases/{uid}/invite` no longer exists, so an inline-created client never gets a login and can never fulfil their steps.
  - No submitted-answers or linked-document review (the fields exist in `lib/flows.ts:129-158` for the deleted v1 API).
  - `ActionBody.participant_contact_id` is optional; omitted on a fanned step, `_write_step_status` updates **every** participant instance of that step key (`orchestrator.py:285-302`) — a mass-completion footgun.
  - Timeline is capped at 50 with no paging and only renders four `step.*` types (`eventLine`).

### Client portal fulfilment loop

- **Status**: ⚠️
- **What it does today**: `GET /applications/me` returns the caller's `assignee='client'` steps across cases via `contact_scope` (`applications.py:360-391`); the portal presents them as one checklist and fulfils through the existing request endpoints, which call `on_item_fulfilled` → `submit_step` → handler → `apply_step_event` → `recompute` → projection (`client/src/routes/onboarding/`, `app/routers/documents.py:1090,1170`, `orchestrator.py:65-107,305-328`).
- **Expected**: As a client I see my outstanding steps, complete them and watch progress update.
  - Only my steps, ordered outstanding-first.
  - Each step type has a working action.
  - Completing a step immediately advances the journey and the projection.
  - Completed journeys show a clear done state.
- **Gaps**:
  - Steps with no request item (`verify_identity` as seeded, `task`) render a "Complete" button that silently does nothing.
  - Everything is flattened into one synthetic "Onboarding" case (`onboarding/+page.server.ts:90-103`), so multi-journey clients cannot tell which application a step belongs to; `flow_name`/`subject_name` are hardcoded.
  - No blocked/waiting messaging: a step waiting on staff shows as "Pending".
  - `/applications/me` is `_require_v2`-gated, so with the flag off the client portal onboarding tab shows a load-error banner.

### KYC dedup via `crm.kyc_checks`

- **Status**: ⚠️
- **What it does today**: At instantiate, an existing `passed` check of the same kind with `valid_until > now()` short-circuits the step to COMPLETED with reason `already_verified`; otherwise a `pending` row is opened (`step_handlers.py:147-178`). On outcome, `valid_until = now() + validity_days` (step config, else 365) (`step_handlers.py:191-221`). The store is RLS-protected and audited (`migrations/sql/0026_kyc_checks.sql`).
- **Expected**: As a compliance officer a person is never re-KYC'd within the validity window, across accounts and applications.
  - Validity window honoured and firm-configurable.
  - Reuse is visible/auditable on the journey.
  - Expiry re-opens the check.
- **Gaps**:
  - The validity window **is** honoured, but `validity_days` is read from the step config at _record_ time, so shortening the window later doesn't invalidate existing passes and nothing scans for expiry — an expired pass is only noticed if a new journey starts.
  - Reuse is invisible: `already_verified` lands in `case_steps.data.last_event` and is never surfaced (the step just shows "fulfilled").
  - Dedup is per (tenant, contact, kind) with no linkage back to which execution consumed it; `pending` rows leak (a cancelled/abandoned journey leaves an eternal `pending` check that `_record_result` may later attach to the wrong journey).
  - No staff view of a contact's KYC history anywhere in the portal.

### Provider adapters (iDenfy / Creditsafe)

- **Status**: 🎭
- **What it does today**: `providers.py` defines a `ReviewProvider` Protocol and registers only `ManualProvider` (a no-op); `get_provider` is exported and **never called**. The step `provider` config is written into `crm.kyc_checks.provider` as a label only (`step_handlers.py:175`).
- **Expected**: As a firm with iDenfy/Creditsafe credentials I configure the provider on the step and checks run automatically.
  - Adapter starts a check at instantiate.
  - A webhook route ingests the result and calls `submit_step`.
  - Credentials configured per tenant, secrets encrypted.
- **Gaps**:
  - No adapter implementations, no webhook route, no credential storage — the seam is documentation, not capability.
  - The editor lets an author type any provider string (`StepInspector.svelte:338-348`), which silently degrades to manual with no warning at publish.

### Cancel / abandon a journey

- **Status**: ❌
- **What it does today**: `State.CANCELLED` exists, `cases.cancelled_at`/`executions.cancelled_at` exist, `_save_case_state`/`_save_execution_state` handle it (`orchestrator.py:552-597`) and the projection maps it to `cancelled`. Nothing ever sets it: there is no cancel endpoint on `applications.py` and the documented `POST /cases/{uid}/cancel` no longer exists.
- **Expected**: As an adviser I abandon a journey started in error, withdrawing its outstanding requests.
  - Cancel from journey detail with a reason.
  - Cases/steps cancelled, request items withdrawn, tasks cancelled.
  - The client's checklist clears.
  - The subject can start a fresh journey afterwards.
- **Gaps**:
  - Unreachable state: `cancelled` in the board/stepper/projection is dead code.
  - `StepHandler.cancel`/compensate is declared in the contract (`handlers.py:71-73`) and implemented nowhere but the no-op base.
  - Combined with `cases_one_active_per_subject`, this **permanently locks a client out of re-onboarding** after any mistake.

### Reopen / monitoring alerts

- **Status**: ❌
- **What it does today**: `reopen_action(adverse, application_status) → none|flag|reopen` is pure and unit-tested (`flows/kyc.py`, `tests/test_flows_kyc.py:54`). No caller, no ingestion route, no `monitoring_ref` writer, no reopen transition (terminal states are latched by design, `states.py`).
- **Expected**: As a compliance officer an adverse monitoring hit on a completed client re-opens or flags the affected applications and raises a task.
  - Alert ingestion endpoint/webhook.
  - Completed application reopened with an audit trail; running one flagged.
  - A compliance task is raised automatically.
- **Gaps**:
  - Entirely unimplemented; 0026's stated purpose is unmet.
  - `TERMINAL` is latched with no documented reopen escape hatch, so "reopen" would need a new transition anyway.

### Behaviour with `FLOWS_ORCHESTRATOR_V2` off (the default)

- **Status**: 💥
- **What it does today**: The flag defaults to `False` (`/Users/gio/gpl/portals/apps/api/app/config.py:33`) and is set only in `apps/api/.env.local`. Off, every `/applications` route 404s via `_require_v2` (`applications.py:33-35`). Critically, **the v1 engine no longer exists** — there is no `app/flows/engine.py`, no `seed.py`, no `/cases*` routes and no `/public/onboarding/start` (only a stale `public_onboarding_max_per_hour` setting at `config.py:36` and the docs).
- **Expected**: With the flag off the previous single-case engine remains authoritative (what `config.py:30-33` and `docs/flows.md` both claim).
  - Journeys still start and run on v1.
  - Client onboarding still works.
  - Flipping the flag is a safe, reversible rollout.
- **Gaps**:
  - Flag-off is not a fallback, it is a **total outage** of the domain: flows can be authored and published but no journey can be started, viewed or fulfilled, and the client portal's onboarding tab errors.
  - `docs/flows.md:81-95` documents an entire `/cases` API surface (list/create/invite/cancel/`/cases/me`/public start) that has been deleted — the doc is materially wrong and `apps/platform/src/lib/flows.ts:129-180` still carries the dead `CaseOut`/`CaseRow`/`CaseStep` types.
  - Journey-raised tasks are dead with the flag off (the only outbox producer is `orchestrator._emit_task_requests`) — confirmed.
  - There is no deployment config setting the flag anywhere outside `.env.local`, so a fresh deploy ships the domain dark.

## TASKS

### Create task (manual / API)

- **Status**: ✅
- **What it does today**: `POST /tasks` resolves a role slug to an id, then `service.create_task` validates assignee-in-tenant and the subject pair, inserts with `ON CONFLICT` idempotency for automated sources, and relies on the audit spine (`routers/tasks.py:198-240`, `tasks/service.py:115-199`). The inbox dialog assigns by team/staff/client with category, priority, due date, description (`routes/tasks/+page.svelte:88-140`, `+page.server.ts:37-66`).
- **Expected**: As a staff member I raise a task for a colleague, a team or a client in a few clicks.
  - Assignee validated against the tenant.
  - Subject linkage optional and validated.
  - Duplicate automated sources collapse.
  - Creation is audited.
- **Gaps**:
  - The create dialog cannot set a subject, so manually-raised tasks can never be attached to a journey or client record — the in-context panels only ever show flow-raised or API-created work.
  - No bulk create, no template/checklist.

### Task list + filters

- **Status**: ⚠️
- **What it does today**: `GET /tasks` filters by status, assignee (`me`/`user:`/`role:`/`contact:`), subject, category, overdue, with keyset pagination (`routers/tasks.py:119-178`). The inbox loads `?limit=200` twice (all + mine) and filters client-side into Mine/All/Done tabs sorted by urgency (`routes/tasks/+page.server.ts:17-34`, `+page.svelte:44-59`).
- **Expected**: As a staff member I work a prioritised inbox and can slice by team, client or overdue.
  - Server-side filters exposed in the UI.
  - Overdue badged and sortable.
  - Pagination beyond the first page.
- **Gaps**:
  - None of the API's filters (category, subject, overdue, status) are exposed in the UI — three hardcoded tabs only.
  - Hard 200-row ceiling with no pagination: past 200 tasks the inbox silently truncates, and "Done" is whatever happened to be in that page.
  - `task_status` is a raw string with no allowlist (`routers/tasks.py:139-141`) — a typo returns an empty list rather than a 422.
  - No grouping by due date despite the doc promising it.

### Complete task

- **Status**: ✅
- **What it does today**: `POST /tasks/{uid}/complete` is ungated by permission and relies on RLS, stamping `completed_by`/`completed_at`/`completion_data` and refusing already-settled tasks (`routers/tasks.py:388-413`, `service.py:224-237`). Available from the inbox, task detail, journey panel and the client home.
- **Expected**: As the assignee I mark my task done, optionally with a note, from wherever I see it.
  - Only open/in-progress tasks can be completed.
  - Clients can only complete their own.
  - Completion is audited and reflected everywhere.
- **Gaps**:
  - `completion_data` is accepted by the API but no surface ever sends it — the "complete with note" in the docs is not built (every caller posts `{}`).
  - Completing a task raised by a journey does not feed back into the journey (no reciprocal step/state effect) — by design, but the journey panel gives no hint of that.

### Cancel task

- **Status**: ✅
- **What it does today**: `PATCH /tasks/{uid}` with `status: cancelled` → `service.cancel_task` (`routers/tasks.py:295-296`, `service.py:240-241`), wired to a confirm dialog in the inbox and on task detail.
- **Expected**: As a staff member I cancel work that is no longer needed, without erasing it.
  - No hard delete (grant excludes DELETE).
  - Cancelled tasks leave the open counts.
  - Reason captured.
- **Gaps**:
  - No cancellation reason field anywhere (nor a column).
  - No un-cancel/reopen — `_transition` only accepts open/in_progress as the source state.

### Reassign / edit task

- **Status**: 🎭
- **What it does today**: `service.reassign_task` and the PATCH field-edit path (title, description, category, priority, due date, open⇄in_progress) are fully implemented (`service.py:244-280`, `routers/tasks.py:274-339`). Neither the inbox nor the task detail page exposes any of it — both `+page.server.ts` files implement only `complete` and `cancel`.
- **Expected**: As a staff member I hand a task to a colleague, adjust its due date or move it to in-progress.
  - Reassign picker on the task detail.
  - Inline edit of due date/priority.
  - "Start" moves it to in-progress.
  - The new assignee is notified.
- **Gaps**:
  - Zero UI: the entire edit/reassign/in-progress capability is API-only — `in_progress` is unreachable for a user, which also makes the `Mine` tab's semantics thinner than intended.
  - `reassign_task` does not clear `notified_at`/`due_soon_notified_at`, so a reassigned task **never emails the new assignee** (`service.py:258-277` vs `notifier.py:170`).

### Journey-raised tasks (emission → outbox → dispatcher)

- **Status**: ⚠️
- **What it does today**: `apply_step_event` calls `_emit_task_requests`, which reads the _pinned_ version's step, matches specs to the signal and enqueues `task.requested` in the same transaction with a deterministic `source_ref` (`orchestrator.py:325-384`). The worker drains the outbox every loop; `_handle_task_requested` resolves role slug → id, fills `{participant_name}`/`{flow_name}`/`{step_title}`, computes the due date and calls `create_task`, dead-lettering permanent authoring errors (`dispatcher.py:40-195`, `worker.py:332-339`).
- **Expected**: As an author "AML failed → compliance review task" happens reliably, exactly once, without ever breaking the step transition.
  - Enqueue is transactional with the transition.
  - At-least-once delivery collapses on `source_ref`.
  - Bad specs dead-letter, never block the flow.
  - Works regardless of feature flags for manual/document sources.
- **Gaps**:
  - **Confirmed dead with `FLOWS_ORCHESTRATOR_V2` off** (the default): the sole producer is the v2 orchestrator, and there is no v1 engine to emit from.
  - Emission is only on `apply_step_event`, not on `instantiate` — a step whose _initial_ signal is WAITING/COMPLETED (dedup'd KYC, `generate_document`) never fires its specs.
  - `{"kind": "user"}` assignment passes `assign_to.user_id` straight through without re-validating at dispatch (unlike roles) — a stale uuid surfaces as a `TaskError`-turned-dead-letter rather than a checked reference.
  - No spec fires anywhere out of the box: the seeded onboarding flow declares no `tasks` (`onboarding_seed.py`).
  - `notifications.deliver_due` (`notifications.py:53-101`) is now an orphaned second drain loop with no caller — duplicate, divergent outbox logic sitting next to `dispatch_due`.

### Task notifier emails

- **Status**: ⚠️
- **What it does today**: A worker-side scanner sends an assign-time notice then a near-due reminder (≤2 days or overdue), resolving recipients per assignee kind, stamping `notified_at`/`due_soon_notified_at` on ≥1 delivery, batch 25, every 60 s — and only when `resend_api_key` is set (`tasks/notifier.py`, `worker.py:342-351`, `migrations/sql/0028_tasks_polish.sql:18-27`).
- **Expected**: As an assignee I am emailed when work lands on me and again as it comes due.
  - One email per task per event, idempotent under retry.
  - Role tasks reach every active holder.
  - Client notices carry a portal link.
  - Nothing sends when email is unconfigured.
- **Gaps**:
  - Resend-gated at the worker level, so on any deployment without Resend the entire task-notification feature is silently absent (no queueing, no backlog — tasks created meanwhile stay unstamped and will all fire at once if Resend is later configured).
  - Staff emails carry **no link to the task** (`_cta` returns None for staff — the platform host is unknown to the API), so a staff notice is not actionable.
  - Reassignment doesn't re-notify (above); due-date changes don't reset the reminder stamp either.
  - "No notifiable recipient" stamps the task as notified and only logs — an unassignable role task is silently swallowed.
  - Bypasses the outbox entirely despite the docs saying phase 4 reuses the `notify.*` path, so there is no retry/dead-letter record for a failed task email.

### Task detail + activity

- **Status**: ⚠️
- **What it does today**: `GET /tasks/{uid}` plus `GET /tasks/{uid}/activity` reads the row-level audit spine (`audit.log` on `tasks.tasks`) oldest-first with actor names (`routers/tasks.py:243-381`); the detail page renders the task, its subject, due phrasing and a humanised trail (`routes/tasks/[uid]/+page.svelte`).
- **Expected**: As a staff member I open a task, see its full history and act on it.
  - Created/reassigned/completed/cancelled all appear with actor and time.
  - The subject links through to the journey/client/document.
  - All actions available from here.
- **Gaps**:
  - Only complete/cancel actions (see reassign above).
  - Subject linking is partial: only `execution` becomes a link (`routes/tasks/+page.svelte:65-67`); `contact`/`account`/`document` subjects are not navigable.
  - Activity is unpaginated and unfiltered — it will grow with every field edit.

### Client action items

- **Status**: ⚠️
- **What it does today**: `GET /tasks/me` returns open tasks assigned to the caller's contacts, RLS-scoped, no permission gate (`routers/tasks.py:95-113`); the client home renders an "N things need your attention" card with inline complete (`/Users/gio/gpl/portals/apps/client/src/routes/+page.server.ts:26-52`, `+page.svelte:57-90`).
- **Expected**: As a client I see everything my adviser needs from me in one place and tick it off.
  - Open items on the home page, ordered by due date.
  - A full list page for more than a handful.
  - Optional note on completion.
  - Overdue items are visually distinct.
- **Gaps**:
  - No client `/tasks` list route — the docs' "and a simple list" was not built, so the home card is the only surface (and it has no overflow behaviour).
  - No due-date or overdue treatment in the client card.
  - No note capture on completion.

### Due dates / overdue handling

- **Status**: ⚠️
- **What it does today**: `due_date` is a plain date; `overdue=true` filters server-side (`routers/tasks.py:151-153`); the client helpers compute `isOverdue`/`dueLabel`/`byUrgency` and the inbox badges overdue and sorts by urgency (`/Users/gio/gpl/portals/apps/platform/src/lib/tasks.ts:41-70`). Near-due drives the reminder scan.
- **Expected**: As a manager I see what is late and chase it before it slips.
  - Overdue count visible on the inbox and in nav.
  - Escalation after N days overdue.
  - Due dates editable.
- **Gaps**:
  - `overdue` is computed in the browser's local timezone against a bare date (`new Date(due_date + 'T23:59:59')`), so overdue-ness differs per user's clock and disagrees with the API's `CURRENT_DATE`.
  - Due dates cannot be edited from any UI.
  - No escalation, no repeated reminders (a single `due_soon_notified_at` stamp means exactly one reminder ever, however late it gets).
  - No nav badge for overdue work.

### Recurring / scheduled tasks

- **Status**: ❌ (known gap, documented out of scope)
- **What it does today**: Nothing. `docs/tasks.md:296-298` explicitly defers it; the schema does not preclude a `schedule` producer writing through `create_task`.
- **Expected**: As a compliance lead an annual review task is raised automatically each year per client.
  - A schedule definition with cadence and target set.
  - Idempotent per period via `source_ref`.
  - Visible/pausable.
- **Gaps**:
  - Unimplemented by decision — note only. The `source_kind='system'` value and the `source_ref` unique index are already the right seam.

## ENGINE HYGIENE

### Outbox dead-letter visibility

- **Status**: ❌
- **What it does today**: `flows.outbox` carries `status`, `attempts`, `last_error`, `next_retry`, `delivered_at` and dead-letters after `max_attempts` or immediately on `PermanentDispatchError` (`migrations/sql/0025_flows_orchestration.sql:83-98`, `dispatcher.py:158-187`). The only signal is `logger.warning("outbox %s dead-lettered: %s")` (`dispatcher.py:171`). No API route, no admin page, no metric, no alert — greps across `app/routers` and both portals return nothing.
- **Expected**: As an operator I see failed side-effects, understand why and retry them.
  - A dead-letter list with tenant, event type, error and payload.
  - Manual retry / requeue.
  - Alert when the dead-letter count rises.
- **Gaps**:
  - Dead letters rot silently: a mis-authored task spec means the compliance follow-up is never raised and nobody is told.
  - No requeue path — a fixed role slug does not resurrect the dead row.
  - `notifications.deliver_due` (orphaned) can also dead-letter with the generic `'delivery failed'`, so error provenance would be ambiguous if it were ever re-wired.

### `domain_events` timeline exposure

- **Status**: ⚠️
- **What it does today**: Every step transition appends an immutable fact (`orchestrator.py:651-676`); `GET /applications/{uid}` returns the newest 50 and the journey page renders them (`applications.py:517-529`).
- **Expected**: As an adviser/auditor I read the full history of a journey, including who acted.
  - Complete, paginated timeline.
  - Actor attribution.
  - Non-step events (started, projected, gate opened, task raised).
- **Gaps**:
  - Only four `step.*` event types are ever written — no `execution.started`, `case.completed`, gate-opened, projection-changed or `task.requested` fact, so the "replayable fact log" claim is thin.
  - No actor on the event payload (`{step_key, participant}` only) — the timeline cannot say who passed an AML check.
  - Hard 50-row cap, no paging, and only `step.*` types are humanised (`eventLine`); anything else renders the raw type.
  - Participant ids are shown as raw step keys, not names.

### `idempotency_key` no-op

- **Status**: ⚠️ (known wart)
- **What it does today**: `_append_event` has `ON CONFLICT (tenant_id, event_type, idempotency_key) DO NOTHING`, but the key is a fresh `evt_<uuid4>` per call (`orchestrator.py:324,651-676`), so the constraint can never fire.
- **Expected**: A re-fired transition appends one fact, not two.
  - Key derived from (execution, case, step, participant, signal[, attempt]).
  - Replaying an event is a no-op.
- **Gaps**:
  - Duplicate facts on any retried transition — the fact log double-counts, and the journey timeline will show the same event twice.
  - Documented as deliberately left alone (`docs/tasks.md:204-207`) because task correctness rides on `tasks_source_ref_key` instead; still a correctness hole in the audit spine.

### Advisory locks

- **Status**: ⚠️
- **What it does today**: `recompute` takes `pg_advisory_xact_lock(execution_id)` before reading cases/steps, so concurrent sibling-case events serialise on the group and the lock is held to commit (`orchestrator.py:110-112,446-449`).
- **Expected**: Concurrent step events on one execution never lose the rollup or deadlock.
  - Lock taken before any state write.
  - One lock key per execution, single lock order.
  - Bounded wait / no lock escalation.
- **Gaps**:
  - The module docstring claims the lock is taken **FIRST**, but `apply_step_event` writes the step status, the domain event _and_ the outbox row before calling `recompute` (`orchestrator.py:315-328`) — the lock protects only the rollup, not the per-step write. Correctness holds via transaction isolation, but the stated invariant is not what the code does.
  - `start_execution` performs the entire plan+persist outside the lock and only locks at the closing `recompute` — two concurrent starts for the same subject race onto `cases_one_active_per_subject` and 500.
  - The key is a bare `bigint` in the shared `pg_advisory_lock` namespace with no class discriminator — collides with any other future advisory-lock user on the same integer.

### Flow version change vs in-flight executions

- **Status**: ⚠️
- **What it does today**: An execution pins `flow_version_id` and every subsequent read of the definition — `submit_step`'s config lookup and `_emit_task_requests`' spec lookup — joins through that pinned version (`orchestrator.py:404-430,344-355`). Publishing archives the old version and repoints only `flows.flows.current_published_version_id` (`flows.py:473-493`), so running journeys are untouched.
- **Expected**: As an author I publish v2 without breaking journeys mid-flight, and I understand what v2 applies to.
  - Running executions finish on their pinned version.
  - New journeys use the new version.
  - The UI tells me how many journeys are on old versions.
  - A step added in v2 can optionally be back-filled.
- **Gaps**:
  - Pinning works, but there is **no migration/back-fill story**: a step added in v2 never appears in a running journey, and a step removed in v2 still blocks one.
  - No visibility: neither the flow editor nor the journeys board shows which version an execution is on, nor how many executions a version has (`GET /applications` does not return the flow version).
  - A step _renamed_ (id changed) in v2 while the old version is archived leaves the archived definition intact — fine — but the seed's practice of publishing unvalidated definitions means an archived version can be structurally invalid, and `submit_step` silently returns on a missing step (`orchestrator.py:420-424`) rather than erroring.

---

## DOMAIN SUMMARY

| #   | Function                              | Status          |
| --- | ------------------------------------- | --------------- |
| 1   | Create flow                           | ✅              |
| 2   | Visual editor — add/delete steps      | ⚠️              |
| 3   | Step connections / `depends_on`       | ❌              |
| 4   | Step visibility authoring             | ✅              |
| 5   | Intake config                         | ⚠️              |
| 6   | Raise-a-task specs                    | ⚠️              |
| 7   | Save draft                            | ✅              |
| 8   | Publish (validation + preflight)      | ⚠️              |
| 9   | Version history / rollback            | ❌              |
| 10  | Archive flow                          | ❌              |
| 11  | Step type — `fill_form`               | ⚠️              |
| 12  | Step type — `accept_terms`            | ⚠️              |
| 13  | Step type — `upload_document`         | ⚠️              |
| 14  | Step type — `setup_account`           | 🎭              |
| 15  | Step type — `verify_identity`         | 🎭              |
| 16  | Step type — `aml_check`               | 🎭              |
| 17  | Step type — `generate_document`       | 💥              |
| 18  | Step type — `review`                  | ✅              |
| 19  | Step type — `task`                    | ⚠️              |
| 20  | "New journey" wizard                  | ⚠️              |
| 21  | Person-only subject path              | ❌              |
| 22  | `start_execution` planning + KYC gate | ⚠️              |
| 23  | Journeys board / status projection    | ⚠️              |
| 24  | Journey detail + staff actions        | ⚠️              |
| 25  | Client portal fulfilment loop         | ⚠️              |
| 26  | KYC dedup (`crm.kyc_checks`)          | ⚠️              |
| 27  | Provider adapters (iDenfy/Creditsafe) | 🎭              |
| 28  | Cancel / abandon journey              | ❌              |
| 29  | Reopen / monitoring alerts            | ❌              |
| 30  | `FLOWS_ORCHESTRATOR_V2` off           | 💥              |
| 31  | Create task (manual/API)              | ✅              |
| 32  | Task list + filters                   | ⚠️              |
| 33  | Complete task                         | ✅              |
| 34  | Cancel task                           | ✅              |
| 35  | Reassign / edit task                  | 🎭              |
| 36  | Journey-raised tasks pipeline         | ⚠️              |
| 37  | Task notifier emails                  | ⚠️              |
| 38  | Task detail + activity                | ⚠️              |
| 39  | Client action items                   | ⚠️              |
| 40  | Due dates / overdue                   | ⚠️              |
| 41  | Recurring tasks                       | ❌ (documented) |
| 42  | Outbox dead-letter visibility         | ❌              |
| 43  | `domain_events` timeline exposure     | ⚠️              |
| 44  | `idempotency_key` no-op               | ⚠️              |
| 45  | Advisory locks                        | ⚠️              |
| 46  | Flow version change vs in-flight      | ⚠️              |

**Totals**: ✅ 6 · ⚠️ 26 · 🎭 5 · ❌ 7 · 💥 2

### Top-5 severest gaps

1. **The seeded onboarding journey cannot actually be completed.** Staff-assigned `fill_form` has no fulfilment surface anywhere (`lib/applications.ts:117-124` omits it; `/applications/me` filters `assignee='client'`; the platform has no document-requests UI), and `review` is likewise missing from `MANUAL_TYPES`, so the offshore compliance review is unactionable. Six fact-find steps plus the offshore gate in `onboarding_seed.py` are dead ends — the flagship flow stalls on its first tier.
2. **Flag-off is an outage, not a fallback.** `flows_orchestrator_v2` defaults to `False` (`app/config.py:33`) and is set only in `.env.local`, but the v1 engine, the `/cases*` API and `/public/onboarding/start` have all been deleted. With the default config no journey can be started, viewed or fulfilled, the client portal's onboarding tab errors, and journey-raised tasks are inert. `docs/flows.md:81-95` still documents the removed surface as current.
3. **A client can never be re-onboarded, and no journey can be cancelled.** `cases_one_active_per_subject` (`migrations/sql/0024_flows_p0_hardening.sql:31`) is enforced by a bare unique index with no `ON CONFLICT` in `_persist_case`, so a second concurrent journey 500s; and since nothing anywhere sets `CANCELLED` (no cancel endpoint, `StepHandler.cancel` unimplemented), a mistaken journey is permanent and locks the subject out for good.
4. **`generate_document` bricks any flow that uses it.** The handler returns `WAITING` and enqueues nothing (`step_handlers.py:360-372`) — no document row, no `render_jobs` insert, no completion callback — so the required step pins the case to `WAITING` forever with no staff override. Two other silent dead-ends compound this: account-scope `accept_terms`/`upload_document` without `per_participant`, and application-scope `fill_form`, all create no artifact and have no actor.
5. **Side-effect failures are invisible and dependencies are decorative.** `flows.outbox` dead letters have no API, UI, metric or retry (only `logger.warning` at `dispatcher.py:171`), so a mis-authored task spec silently loses the compliance follow-up; and `depends_on` — authored in the seed, validated, planned and persisted — is read by nothing, so every step (including a `crm.kyc_checks` pending row and the client's identity step) is opened simultaneously regardless of declared prerequisites.
