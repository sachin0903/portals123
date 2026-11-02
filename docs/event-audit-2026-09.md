# The event audit — September 2026

Every one of the 221 action events, judged on two questions:

- **Context** — can the row ALONE say what happened, to which record, and what changed?
  `PASS` · `THIN` (names the record or the change, not both) · `USELESS` (says a type happened).
- **Exists?** — would a firm or a regulator ever look for it?
  `KEEP` · `NOISE` (nobody will search for it, and it buries the rows that matter) · `MERGE`.

Judged on the SENTENCE a reader sees in the collapsed row, not on what the code captures. Entity
chips, `metadata.body` and `metadata.changes` are all inside the expansion, so a row that does not
name the record in its sentence does not name it on the page.

Read against the live route table, `pkgs/ui/src/lib/components/audit/describe.ts` and
`apps/api/app/event_schema/registry.py`.

---

## Read this first — what the trail holds of a client's own data

**This reversed once, and the second decision is the one in the code.** On 23 September the call
was "keep the capture, review later", on the reasoning that anyone who may read the trail may
already open the client record. That does not hold for the RAW stream, which feeds notifications
and automations as `service_role`, outside any row-level read scoping at all — so no answer about
who may hold `activity:read` reaches it. The three worst fields are therefore masked at CAPTURE
time, and the read-scope question is no longer load-bearing.

| Event                           | Field                                        | What lands                     |
| ------------------------------- | -------------------------------------------- | ------------------------------ |
| `client.bank_account_updated`   | `sort_code`, `account_number`, `iban`, `bic` | the last four only (`***3456`) |
| `account.bank_accounts_updated` | the same, nested per account                 | the last four only             |
| `client.vulnerability_updated`  | the free-text `notes`                        | nothing — dropped whole        |

A masked form rather than nothing, for the bank fields: it is still useful to see that AN account
changed, and roughly which. A vulnerability note has no safe masked form — its last four
characters can be the content — so it goes entirely, and the structured
`is_vulnerable` / `is_temporary` / `reasons` already say what changed.

Scoped BY EVENT TYPE, never by a global keyword: `notes` and `iban` are ordinary field names on
other events (`client.note_added`'s body IS its note), and a global match would gut them too.

`redact` / `redact_deep` do not catch these and were never meant to: `REDACTED_MARKERS` is
`password`, `secret`, `token`, `authorization`, `credential`, `api_key` — a CREDENTIAL belt. A sort
code is not a secret, it is a client's data, so it needs its own.

### What is still held, and still to revisit

The belt covers the three worst fields, not every personal one. A client's **date of birth, tax
position, nationality and national identifiers** still land verbatim on `client.updated`, because
that event's whole content is the change and masking it would leave a row saying nothing.

So the read-scope question is narrower now, but not closed: `activity:read` is firm-wide, where
`crm.contacts` is scoped by `auth.contact_scope()` for an adviser. A firm running adviser scoping
still hands every reader of `/logs` a client book the record itself withholds — just without
the bank details in it. The fix is RLS on `audit.actions`, or an equivalent app-level scope, and it
is not built.

---

## The numbers

|                                            |     |
| ------------------------------------------ | --- |
| Events audited                             | 221 |
| `PASS`                                     | 58  |
| `THIN`                                     | 104 |
| `USELESS`                                  | 59  |
| Flagged as noise in the first cut          | 45  |
| …of which actually removable               | 16  |
| Proposed `MERGE` (fold into another event) | 17  |

Two thirds of the trail cannot name what it acted on. Almost none of it should be DELETED —
see below.

---

## Nothing is deleted. Two tabs instead.

The first cut said "delete 44". That was wrong twice over, and both corrections came from the same
place: **the event stream is a substrate, not a report.**

- **It is what notifications and automations fire on.** `flows/triggers.py` tails `audit.actions`
  and projects it onto a curated menu of 20 named triggers. Deleting an event type does not just
  remove a row — it removes a trigger a firm could be given. (Measured: none of the 45 candidates
  feeds a trigger _today_, but the notification registry lists all 207 events precisely so that any
  of them can be wired without new capture. Deleting one closes that door for good.)
- **Several of the "duplicates" were not duplicates**, and the test I used had a hole — see the
  trap below.

So: keep every event, and sort them. `/logs` has two tabs.

| Surface     | What it holds                                   | Who reads it                                |
| ----------- | ----------------------------------------------- | ------------------------------------------- |
| **Events**  | everything the platform recorded, raw, complete | the trigger consumer, an investigation, ops |
| **Actions** | what a PERSON deliberately changed              | the default surface a firm scans            |

**A PARTITION, not a filter and its superset.** `ROUTINE_EVENT_TYPES` is the split: a row in the
set is Audit, a row outside it is Actions. Every row is in exactly one, which is what makes this
different from hiding — nothing is withheld from anyone, and neither tab has to confess that it is
filtered. The earlier design was one list with a link that widened it, and it needed a running
count ("+ 4,093 hidden") to stay honest. Two named tabs make the count unnecessary: the other half
is visible, labelled, and one click away.

Contrast `routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES`, which hides the whole `aml.` family from
a firm. That one genuinely withholds rows, and it is a security control. This is not.

**The vocabulary problem, settled.** "Action" was taken three times over — `audit.actions` the
table, `ActionEventsMiddleware`, `ActionLog` the component, and the admin `/audit` page's own
"Actions" tab. The table name is internal and stays. The surface is **Logs**, and its tabs are
**Actions** and **Audit**, so no two user-facing things are called the same thing. The decorator
that meant "write no row at all" was renamed `writes_no_row` for the same reason: `not_an_action`
would now read as "put this in the Audit tab".

### What makes an event an action

Three tests, all of which must hold:

1. **A person did it** — not a clock, not a worker, not a poller.
2. **Something durable changed** — not a read, not a preview, not a dry run.
3. **The change was the point** — not a side effect of copying somebody else's data into ours.

### The trap that caught four of these

A `created_by` or `updated_by` column is not a history. It satisfies "already recorded" ONLY where
the record gains a row PER CHANGE, or where the value is written once and never moves
(`notifications.read_at`). Two shapes look like they qualify and do not:

- **one row per record** — `l10n.overrides` is unique on `(tenant_id, term_key)`, so `updated_by`
  is overwritten on every rename and answers only "who set it last";
- **a draft row**, which is a mutable buffer rather than a version. `flows.save_flow_version` says
  so in its own docstring — _"Updates the existing draft, or forks a new draft version if the
  latest is published (mirrors the templates editor)"_ — and the statement is a bare
  `UPDATE flows.flow_versions SET definition = …`. `created_by` records who FORKED the draft, once;
  every edit after that, by anyone, any number of times, leaves no trace. The two template version
  tables have no `updated_at` at all, so they cannot even say when.

So `terminology.updated`, `template.version_saved`, `email_template.version_saved` and
`flow.version_saved` are each the ONLY record of who changed some client-facing wording. All four
are actions.

### Event only — 26

Recorded, kept, findable, and off the Actions surface.

**No person did it** (a clock or a worker): `task.due_soon` — and it stays exactly because a firm
may want to notify on it.

**Nothing durable changed** (a read, a preview, a dry run, a socket):
`notification.read`, `notification.dismissed`, `notification.all_read`,
`notification.all_dismissed`, `conversation.read`, `document.viewed`,
`notification.stream_opened`, `message.stream_opened`, `template.preview_rendered`,
`template.preview_pdf_rendered`, `email_template.previewed`, `flow.simulated`, `automation.tested`

**The change was a copy, not a decision** — re-pulls, rebuilds, reconciliations and job control:
`aml.sync_ran`, `firm.synced`, `firm.roles_synced`, `firm.usage_refreshed`,
`firm.provider_data_refreshed`, `firm.provider_refresh_resumed`, `platform.all_firms_refreshed`,
`platform.rebuilt`, `environment.registry_refreshed`, `document.render_retried`,
`flow.dead_letter_replayed`

`document.viewed` is the one to watch: it is a read, so it is not an action, but it is also one row
per GET on a client's file. De-duplicate it per actor per document per day or it swamps the events
surface too.

### Action — 19

A person, a deliberate change, the firm's own decision.

| Event                                                                          | Why it is an action                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `terminology.updated`                                                          | The only record of a firm's client-facing wording changing — `l10n.overrides` keeps no history. Needs two lines of capture first: today the row holds `{key}` and NOT the old or new word, and a blank label CLEARS the override, so a rename and a reset write the identical row |
| `template.version_saved`                                                       | A draft is a buffer, not a version — the stream is the only edit history, and it already keeps the body                                                                                                                                                                           |
| `email_template.version_saved`                                                 | The same, for wording that mails every client in the book                                                                                                                                                                                                                         |
| `flow.version_saved`                                                           | The same, for the definition clients are put through                                                                                                                                                                                                                              |
| `client.note_added`                                                            | **`crm.contact_notes` has no `created_by` at all** — the only record of who wrote a note on a client                                                                                                                                                                              |
| `content_category.created`                                                     | `content.categories` has no `created_by` either                                                                                                                                                                                                                                   |
| `member.invite_resent`                                                         | An access act; nobody looks for it until an account is taken over                                                                                                                                                                                                                 |
| `branding.updated`                                                             | `auth.tenants.theme` keeps no history                                                                                                                                                                                                                                             |
| `branding.asset_uploaded`                                                      | The same, for the logo itself                                                                                                                                                                                                                                                     |
| `document.render_cancelled`                                                    | Somebody stopped a client document being produced                                                                                                                                                                                                                                 |
| `firm.storage_provisioned`                                                     | An ops act on a firm; nothing else names the operator                                                                                                                                                                                                                             |
| `firm.storage_provisioned_for_all`                                             | The same, estate-wide                                                                                                                                                                                                                                                             |
| `notification_rule.defaults_loaded`                                            | Rules that now exist and did not before                                                                                                                                                                                                                                           |
| `email_template.defaults_loaded`                                               | Wording that now exists and did not before                                                                                                                                                                                                                                        |
| `email_template.preset_provisioned`                                            | The same                                                                                                                                                                                                                                                                          |
| `email_template.image_added`                                                   | An asset that ends up in a client's inbox                                                                                                                                                                                                                                         |
| `content.image_added`                                                          | Part of what was published to clients                                                                                                                                                                                                                                             |
| `content.hero_uploaded`                                                        | The same                                                                                                                                                                                                                                                                          |
| `notification.preferences_updated` / `notification.client_preferences_updated` | `notifications.preferences` holds `updated_at`, no actor and only the CURRENT value — the only answer to "they say they were never told; did they switch it off, and when?"                                                                                                       |

### What this replaces

Delete **nothing**. Surface 19 of the 45 as actions and leave 26 on the events stream.

The three draft-save events carry their bodies, so keeping them means the stream holds every
autosave of a template, an email and a flow definition, each up to `MAX_STORED_BODY_BYTES`. That is
what makes them a usable edit history, and it is the largest thing here — a retention rule, not a
deletion.

(The "44" in the first cut was also a miscount: the list held 45.)

## Fold these away — 17 merges, but check the trigger first

**Two of these feed a live notification trigger, and merging them breaks it.**
`journey.started` fires `application.started`, and `document_request.form_submitted` fires
`client.form_submitted` — which is one of the eight trigger keys this deployment's
`notifications.rules` actually uses. Fold the event only if the trigger is re-pointed at the
survivor in the same change.

| Fold                                       | Into                            | Why                                                                                                   |
| ------------------------------------------ | ------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `journey.started`                          | `journey.created`               | Fires seconds later on the same application                                                           |
| `journey.step_retried`                     | `journey.step_actioned`         | The outcome is already written there                                                                  |
| `contribution.refreshed_from_morgan_lloyd` | `contribution.status_updated`   | An idempotent poll; the transition it finds belongs on the status row                                 |
| `transfer.refreshed_from_morgan_lloyd`     | `transfer.status_updated`       | Same                                                                                                  |
| `automation.reordered`                     | `automation.updated`            | Already captures `priority`                                                                           |
| `task.assigned`                            | `task.updated`                  | Restates an assignment already recorded                                                               |
| `document.rendered`                        | `document.generated`            | The same document a few seconds later                                                                 |
| `document_request.form_submitted`          | `form.response_submitted`       | One act, two names, neither naming the form                                                           |
| `template.duplicated`                      | `template.created`              | A duplicate IS a create, and folding forces the new uid onto the row                                  |
| `template.unarchived` / `form.unarchived`  | `*.updated`                     | Archiving already rides on update; one half having its own event reads as if nothing is ever archived |
| `form.fields_updated`                      | `form.published`                | A builder autosave is not an act                                                                      |
| `message_group.document_requested`         | `document_request.created`      | Inherits that event's captures                                                                        |
| `account.synced_to_morgan_lloyd`           | `client.synced_to_morgan_lloyd` | The handler's own docstring says it is the same call, narrowed                                        |
| `client.pushed_to_morgan_lloyd`            | `client.synced_to_morgan_lloyd` | The route is superseded; two names split the history                                                  |
| `member.provider_ref_updated`              | `member.profile_updated`        | The same act — a firm restating who a member is                                                       |
| `platform.sync_started`                    | `platform.all_firms_refreshed`  | The same button, and a START is not an effect                                                         |
| `model.commentary_created`                 | `model.commentary_updated`      | One service call (`upsert_commentary`) behind two names                                               |

---

## The twelve worst rows

Ranked by consequence × how little the row says.

1. **`aml.hit_decided`** — "Decided an AML hit". Graphene's own regulatory record. No client, no
   match, no decision, no reason — and `subject.decision` and `body.reason` are already on the row.
2. **`platform.action_ran`** — an operator runs a named action against one firm's custodian data.
   `{provider}`, `{key}` and the firm are all on the row and the sentence reads none of them.
3. **`environment.secrets_updated`** — rotates a firm's WorkOS and storage secrets, and it is the
   only ops act with no firm chip and no proxy badge, because its path carries `environment_uid`
   rather than `tenant_uid`.
4. **`account.beneficiary_removed`** — withdraws a death-benefit nomination. Names neither the
   nominee, the account, nor the share, though both entities resolve.
5. **`member.invitation_revealed`, `member.mfa_reset`, `member.password_reset_sent`** — three
   ops-proxied account-takeover paths, all anonymous, all with the login resolved and discarded.
6. **`fund_composition.imported`** — a ten-workbook bulk import that records literally nothing.
7. **`model.version_published`** — rebalances every account on the model; names neither model nor
   version. `model.version_created` captures nothing at all.
8. **`client.note_deleted`** — the only surviving trace of a destroyed file note, and it identifies
   neither the note nor the client.
9. **`file.uploaded`** — "Uploaded a file". Deletions can be traced; arrivals cannot.
10. **`journey.created`** — the handler returns the journey uid and nobody takes it. `journey.started`
    is worse: its one id (`app_uid`) is resolved against `flows.executions`, which is the wrong
    table, so the chip is a bare uid.
11. **`account.drawdown_session_started`** — mints a live drawdown link at the vendor against an
    unnamed pension.
12. **`firm.created`** — the only event that creates a tenant, and the tenant cannot be identified
    from the row.

---

## The cheapest fixes

Ordered by events moved per line of code.

1. **Lift the created record's uid off the 2xx** (`_resp_id` / `_resp_ids` in
   `event_schema/registry.py`, exactly as `client.created` and `document.uploaded` already do).
   `namedSubject` then names the record with no builder at all. Covers **twenty-plus** events:
   `journey.created`, `flow.created`, `automation.created`, `task.created`, `transfer.created`,
   `file.uploaded`, `document.generated`, `document.batch_generated`, `document_request.created`,
   `template.created`, `form.created`, `dataset.created`, `email_template.created`,
   `content.created`, `message_group.created`, `model.created`, `product.created`,
   `fee_package.created`, `provider.created`, `account.beneficiary_added`.
2. **One `ENRICHED` builder that prefixes the account label** onto the seven multi-word `account.*`
   sentences — the entity is already resolved on every one. Equivalently: relax `namedSubject` to
   allow a qualifier and append it, which would fix this class across the whole trail.
3. **One shared `memberActSentence`** weaving the resolved login into `member.mfa_reset`,
   `member.password_reset_sent`, `member.invitation_revealed`, `member.invitation_revoked` and
   `member.profile_updated` — five anonymous security rows.
4. **Two lines in `RESOURCE_ENTITY_KIND`**: `storage_credential` and `environment`. Both kinds
   already have resolvers and both params are already in `_ENTITY_FIELD_KINDS`.
5. **Read the captures that already exist.** No new capture, no schema change:
   `client.vulnerability_updated` (`is_vulnerable` + `reasons`), `firm.created` (`alias`),
   `firm.currency_settings_updated` (`base_currency`), `firm.ai_settings_updated` /
   `firm.provider_updated` (`enabled`), `contribution.created` (`amount` + `category`),
   `transfer.created` (`total_value` + `safeguarded_benefits`), `instruction.created`
   (`kind` + `amount`), `journey.step_actioned` (`outcome` + `step_key`),
   `platform.action_ran` (`provider` + `key`), `terminology.updated` (`key`),
   `role.permissions_updated` (name the first slug, not just the count).
6. **Delete the four hard-coded `message_group.*` builders** in `describe.ts` — they return fixed
   strings and block `namedSubject`, so the group name that IS resolved is thrown away.
7. **A version resolver.** Capture `version_uid` on the version routes and add a resolver — fixes
   `template.version_published`, `email_template.version_published`,
   `email_template.version_restored`, `form.published`, `model.version_created` and
   `model.version_published` together.
8. **Fix `_ENTITY_FIELD_KINDS["app_uid"]`** — it names a `crm.applications` uid while the `journey`
   resolver reads `flows.executions`, so the id resolves to nothing.
9. **`notification_rule.deleted` is structurally unfixable as written** — the resolver reads a row
   the act hard-deletes, so the label is ALWAYS null. It needs the label pre-read into
   `metadata.previous`.
10. **Two before-value holes**: `provider.updated` is the only catalogue update absent from
    `CHANGED_COLUMNS`; and `fee_package.fees` exceeds `MAX_CHANGED_VALUE_CHARS`, so it is stored as
    its shape and a rate change is not recorded as a change.

---

## Every event

The per-event table that produced the lists above is not carried here. It was a working
document of 221 rows, reproducible from the route table at any time, and it went stale the
moment main added an event — which it did four times during this change alone. What it
decided is above; how it decided is `app/event_schema/classify.py` and the gate that reads it.
