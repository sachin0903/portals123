# Morgan Lloyd — Workflows

Six workflows keep Graphene, SEI (custody), and Morgan Lloyd (scheme) consistent. Colour legend from the vendor diagrams: Client/Adviser (green), Graphene (orange), Morgan Lloyd (blue), SEI (purple), CTC (grey), API call (red arrow).

---

## A. Account Open (new SIPP application)

**Goal:** From a completed onboarding, open a SEI Third-Party account **and** a Morgan Lloyd SIPP; auto-populate ML's ContactID + Scheme Reference back into Graphene.

```
1. Client meets IFA, agrees to open a Graphene pension.
2. Adviser completes the Graphene Onboarding App (personal + account + beneficiaries + transfers + investment).
   └─ (Illustration produced via CTC — see workflow B.)
3. On submit, Graphene calls SEI (create investment account) AND Morgan Lloyd (create SIPP).
4. ML creates Contact → Scheme → Beneficiaries(N) → Transfers(M) → Investment → Documents.
5. ML returns ContactID + Scheme Reference Number → Graphene stores them and marks the application complete.
```

**ML call sequence (idempotent saga):**
`cam_createcontact` → `cam_createscheme` (schemeType=16, sippType=809130016) → `cam_createbeneficiary` ×N → `cam_createtransfer` ×M → `cam_createinvestment` (provider GUID, policyNumber) → `cam_submitdocument`. ML then processes the SIPP asynchronously; poll with `cam_getschemebyschemref`.

**Decision point:** _"Does the client wish to take benefits immediately?"_ → if yes, branch to the Benefits Application (workflow E).

---

## B. Illustration (CTC)

**Goal:** Produce a Key Features Illustration PDF (see `Scheme_Illustration EXAMPLE.pdf`) and display it to adviser/client.

```
Graphene ──(API call to CTC: full breakdown of funds + client info, as bespoke XML v1.6)──▶ CTC
CTC processes the calculation ──(response: link to PDF)──▶ Graphene displays the PDF.
```

Persist the PDF to MinIO + `onboarding.documents`; optionally push to ML via `cam_submitillustration`. The XML request schema is documented in [data-mapping.md](./data-mapping.md#ctc-bespoke-xml-v16) (vendor spec: `XML_v1.6_Bespoke.pdf`). Illustration input fields are listed in the "Illustration Fields required" tab of `ML Account Open Workflow 19.5.26.xlsx`.

---

## C. Contributions (+ HMRC Relief at Source)

**Goal:** Record contributions paid into SEI against the ML scheme; ML reclaims tax relief (RAS) for personal contributions and remits it to SEI.

```
1. Client/adviser sends funds to the relevant SEI bank account (single or regular).
2. Graphene builds a daily report of credits into SEI ML accounts (from daily analytics / transaction reporting).
3. Adviser confirms each contribution: personal vs employer, one-off vs regular.
4. Graphene ──(API: create pension contribution)──▶ Morgan Lloyd.
5. ML creates the contribution; if personal → applies for Relief at Source from HMRC.
6. ML sends the tax-relief funds to SEI once received.
```

**Contribution lifecycle:** `Identified → AwaitingConfirmation → Confirmed → MLRecorded → RASApplied → RASReceived → TaxReliefReceivedAtSEI → Reconciled` (see [state-machines.md](./state-machines.md)).
**ML ops:** `cam_createpensioncontribution`, `cam_getreceivedcontributions`, `cam_updatecontributionpension_status`, `cam_uploadpensioncontributions` (bulk). Employer contributions are gross; personal contributions are net of basic-rate relief.

---

## D. Transfers

**Goal:** Execute pension transfers-in to the ML scheme and reconcile the cash into SEI.

```
1. Adviser completes transfer details (in onboarding or standalone).
2. Routes to ML via API ──▶ Morgan Lloyd creates the transfer and follows it through to completion.
   (An FYI Zendesk ticket is raised to Graphene, preserving today's process.)
3. ML advises Graphene of the cash settlement date (in advance).
4. Graphene creates an ACR (Automated Cash Receipt) in the SEI platform and monitors to reconciliation.
5. Graphene informs the adviser of settlement via the app.
```

**ML ops:** `cam_createtransfer`, `cam_gettransfer`, `cam_gettransferbypolicyno`, `cam_getalltransfersbysceme`, `cam_updatetransfer`. Track pending / in-flight / complete; raise a reconciliation break on mismatch.
**Open question (per `ML Transfer Work Flow 26.5.26.xlsx`):** does settlement confirmation arrive via API/webhook or manually? Confirm with ML. Cash vs in-specie and full vs partial transfers must both be supported.

---

## E. Drawdown / Benefits

**Goal:** Let a client draw benefits from their ML SIPP; pay the client and surface documents in Graphene.

```
1. Client wishes to take benefits → Graphene ──(API: Create Benefit Application)──▶ ML.
2. ML response returns an ssoURL → Graphene SSO logs the user into CTC DCube for that client.
3. DCube loads the initial page; ML "Retrieve Member Details" returns client details / pension values / illustration.
4. Client completes the online benefits journey in DCube (or a Graphene drawdown form).
5. Application submitted → ML "Submit Document" (application form / updated illustration) → documents sent to Graphene's SFTP.
6. ML "Drawdown Setup" (application details + client bank details) → ML processes benefits and pays the client.
```

**ML ops:** `cam_retrievememberdetails`, `cam_createbenefitapplication` (returns `ssoLink`), `cam_drawdownsetup`, `cam_getbenefitevents`, `cam_canceldrawdown`, `cam_payrequest`, `cam_validatesso`. Documents arrive via **SFTP** (Graphene must ingest them).
**DCube questions to capture/route** (from `Work Flow - touch points (1).xlsx`, "Dcube questions" tab): withdrawal type; _has advice been given?_ (No → hard stop) + FCA reference; Pension Wise guidance; allowance enhancement factors; protected retirement age / scheme-specific lump sum; benefits taken pre-2006 / 2006–2024; LSA used since April 2024; protections; fully crystallise?; PCLS (£/%); Single / UFPLS / FAD / Regular income / Regular PCLS / Regular UFPLS.

---

## F. Daily Valuations (AUM feed) — first to build

**Goal:** Push end-of-day account values (split crystallised/uncrystallised) to ML each business day so ML maintains accurate per-SIPP valuations.

```
1. End-of-day investment value processed by Graphene (from daily analytics).
2. Graphene ──(API: Create Policy Valuation)──▶ Morgan Lloyd.
3. ML processes all fund values/transactions from the previous day.
4. ML updates the crystallised/uncrystallised split for each SIPP. → wait for next working day → repeat.

On demand: client/adviser wants the crystallised/uncrystallised split
   → Graphene ──(API: Get Scheme by Scheme Ref)──▶ ML → split returned → displayed to client/adviser.
```

**ML ops:** `cam_createpolicyvaluation` (push), `cam_getschemebyschemref` (read split). Idempotent per account+date. This is the smallest standalone slice and is the **Phase 1** deliverable: it proves the shared foundations (ML auth client, config, valuations table, scheduler, queue worker, reconciliation) without saga/SFTP/UI complexity.

---

### Reconciliation (cross-cutting)

Daily three-way reconciliation across Graphene book / SEI custody / ML scheme: break detection, ageing, exception workflow, ops console (saga state, manual-intervention queue, contribution-confirmation queue, drawdown-event queue, polling health, recon breaks), and audit search by client / correlation id / ML id / SEI id.

document generation - client documentation

- cash warning 15%
- annual valuation
