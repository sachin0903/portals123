/** The transfers workbench — the platform portal's `/transfers` page.
 *
 * A **subpath** export (`@graphene/ui/book/transfers`) for the reason `book/tasks` is: the names
 * here are generic enough to collide (`Transfer`, `StatusBadge`-style names elsewhere in the
 * package).
 *
 * This used to also carry an "ops PROTOTYPE" — `types.ts`/`status.ts`/`data.ts`/`ops.ts`/
 * `store.svelte.ts` plus 19 PascalCase components modelling a nine-stage cross-custodian saga
 * against `0003_pensions_ledger.sql`, a schema superseded by 0051 that never shipped. It was a
 * near-duplicate of `apps/admin/src/lib/transfers/` (same file names, drifted independently — a
 * tenant-branding fix landed in admin's copy, a reconciliation crash guard landed in this one) and
 * had no importer outside its own tests, so it was deleted rather than reconciled; the crash guard
 * (an empty `legs` array in the reconcile op) was ported to admin's copy first.
 *
 * Anything new builds on `api.ts`. See its header for what each provider really publishes.
 */

export { default as TransfersWorkbench } from './transfers-workbench.svelte';
export { default as TransfersSkeleton } from './transfers-skeleton.svelte';
export { default as TransferForm } from './transfer-form.svelte';
export { default as SagaRail } from './saga-rail.svelte';
export { default as SagaStrip } from './saga-strip.svelte';
export * from './api.js';
export { stepRefusal, transferActionPath } from './step-path.js';
export type { StageSummary, TransferStepId } from './stages.js';
