/** The contributions workbench — the platform portal's `/contributions` page (nav: "Contributions").
 *
 * A **subpath** export (`@graphene/ui/book/contributions`), not on the package root, for the
 * reason `book/tasks` and `book/journeys` are: the names here are generic enough to collide
 * (`StatusBadge`, `WorkQueue`, `KpiStrip`, `Contribution`).
 *
 * The portal waterfall: data arrives as typed props from the app's loader, every mutation is an
 * injected callback, and an absent callback hides its control — so the admin portal can mount
 * the same workbench read-only over its own `?tenant=`-scoped loaders.
 */

export { default as ClassifyPanel } from './classify-panel.svelte';
export { default as ContributionForm } from './contribution-form.svelte';
export { default as GrossUp } from './gross-up.svelte';
export { default as KpiStrip } from './kpi-strip.svelte';
export { default as MlStatusBadge } from './ml-status-badge.svelte';
export { default as ReconciliationPanel } from './reconciliation-panel.svelte';
// Not mounted by the workbench (one queue, no tabs), but kept and exported: both render real
// server-computed positions, and the relief monitor is what a RAS view becomes the day SEI
// gives us a batch identifier to hang it on.
//
// `PipelineStrip` and `ExpectationBadge` were kept on that reasoning too and it did not hold, so
// they are DELETED. The strip was a second row of the same counts AND a second way to narrow by
// stage; the two had already drifted (it folded `ras_applied` with `ras_received`, the KPI strip
// did not), which is why the control moved into the queue's toolbar and the strip was un-mounted
// (docs/contributions.md §8). Left exported, it was an unreachable second answer to "how much is
// at each stage" that still had to be kept in step with the live one — and it was edited in the
// same batch that un-mounted it. The badge asked whether the custodian had been told to EXPECT
// money that has already arrived, and rendered a bare "—" on every detected row; the panel shows
// the payment's own provenance instead, which `classify-panel.test.ts` pins by NAME.
export { default as CreditQueue } from './credit-queue.svelte';
export { default as ReliefMonitor } from './relief-monitor.svelte';
export { default as StatusBadge } from './status-badge.svelte';
export { default as WorkQueue } from './work-queue.svelte';
export { default as ContributionsWorkbench } from './contributions-workbench.svelte';
export { default as ContributionsSkeleton } from './contributions-skeleton.svelte';

export * from './types.js';
export * from './status.js';
export * from './relief.js';
export * from './detected.js';
