// The rule tier's shared surfaces — the platform portal's /automations pages are thin mounts over
// these, and a future admin (per-tenant) view mounts the same components read-only over its own
// loaders. Loaders and form actions stay app-side; the components take rows in and take their
// hrefs/mutations from injected `links`/`actions`.
//
// Deliberately NOT re-exported from the package root: `TRIGGERS`, `OPERATORS`, `FIELDS`,
// `toModel`/`toJson` and friends are far too generic for the `@graphene/ui` namespace and would
// collide on sight. Consumers import the subpath `@graphene/ui/automations` (see pkgs/ui
// package.json `exports`), exactly as `@graphene/ui/book/listing` and `book/journeys` do.
export { default as AutomationsListing } from './automations-listing.svelte';
export { default as AutomationDetail } from './automation-detail.svelte';
export { default as AutomationSheet } from './automation-sheet.svelte';
export { default as ActionConfigFields } from './action-config.svelte';
export { default as ConditionBuilder } from './condition-builder.svelte';
export { default as DryRunPanel } from './dry-run-panel.svelte';
// The per-condition verdict tree, extracted out of DryRunPanel so the flow tier's dry run renders
// branch arms and step visibility with the identical treatment (one `conditions.explain` shape,
// one renderer).
export { default as ExplainTree } from './explain-tree.svelte';
export { default as RunLog } from './run-log.svelte';

export * from './contract.js';
export * from './conditions.js';
export * from './summary.js';
export * from './types.js';
