# Morgan Lloyd — State Machines

## Application status (existing — reused)

`pkgs/db/src/application-state.ts` (`APPLICATION_STATUSES`, `VALID_TRANSITIONS`, `recomputeAndApplyApplicationStatus`/`progressApplicationStatus`/`transitionApplicationStatus`):

```
draft → with_client → {identity_under_review, aml_under_review, offshore_under_review} → processing → completed
                                                                                            └→ sync_failed → processing | completed
(any non-terminal) → cancelled
```

Status is **derived from facts** (adviser declaration, terms, identity, AML, offshore). Account Open hooks in at `processing`: the ML saga runs via the sync worker; success → `completed` (+ event), permanent failure → `sync_failed` (+ manual intervention). Add ML event types to `STATUS_EVENT_MAP` / the `pkgs/events` inventory (e.g. `application.ml.synced`).

## Account Open saga (new — resumable, idempotent)

Not a status enum; a step sequence guarded by `platform_links` presence (skip any step whose external id already exists):

```
createContact → createScheme → createBeneficiaries(N) → createTransfers(M) → createInvestment → submitDocument → (ML async) schemeActive
```

Re-running resumes from the first missing link. Terminal states: `complete` / `manual_intervention`.

## Contribution lifecycle (new table `contributions`)

```
Identified            credit seen in SEI daily report / cam_getbanktransactions
  → AwaitingConfirmation   surfaced to adviser
  → Confirmed              adviser sets personal/employer, single/regular
  → MLRecorded             cam_createpensioncontribution succeeded
  → RASApplied             ML applied for Relief at Source (personal only)
  → RASReceived            ML received tax relief from HMRC
  → TaxReliefReceivedAtSEI tax-relief cash landed at SEI
  → Reconciled             three-way match complete
```

Employer contributions skip RAS (paid gross): `Confirmed → MLRecorded → Reconciled`. Failure at any step → `manual_intervention` (recorded as a recon break).

## Transfer lifecycle (new table `transfers`)

```
Entered → SubmittedToML → InFlight → MLComplete → SettlementDateAdvised → SEIAcrCreated → Reconciled → AdviserNotified
```

Cash vs in-specie and full vs partial are attributes, not states. Break → `manual_intervention`.

## Drawdown / Benefit application (new table `benefit_applications`)

```
Initiated → BenefitApplicationCreated(ssoLink) → MemberDetailsRetrieved → JourneyInProgress (DCube/Graphene form)
  → Submitted → DocumentsReceived(SFTP) → DrawdownSetup → Paid
  → Cancelled (cam_canceldrawdown)
```

Hard stop if _advice given = No_. Track ML benefit events via `cam_getbenefitevents` (nextPaymentDate, status).

## Policy valuation (new table `policy_valuations`) — Phase 1

```
Computed (analytics) → Queued → Pushed (cam_createpolicyvaluation, mlValuationId) → MLProcessed → Reconciled
                                                                                         └→ Failed → retry / manual
```

Idempotency key = `accountId|valuationDate`; re-runs are no-ops once `Pushed`.

## Reconciliation break (new table `reconciliation_breaks`)

```
Open → Investigating → Resolved | Accepted
```

Scope: graphene-vs-SEI, SEI-vs-ML, graphene-vs-ML. Carries expected vs actual, ageing, assignee. Surfaced in the ops console.
