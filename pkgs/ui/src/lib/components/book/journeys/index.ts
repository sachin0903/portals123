// The shared journeys (flow/case) vocabulary + surfaces for the portal waterfall: the platform
// portal's /journeys pages and the admin portal's per-tenant view render the same book components.
// Flow authoring lives in the focused `@graphene/ui/flow-editor` subpath; this subpath owns the
// run-a-case vocabulary and surfaces.
//
// Deliberately NOT re-exported from the package root: the flow vocabulary is wide and several
// names (`CatalogModel`, `CaseRow`…) would collide with or crowd the `@graphene/ui` namespace.
// Consumers import the subpath `@graphene/ui/book/journeys` (see pkgs/ui package.json `exports`),
// exactly as `@graphene/ui/book/listing` does.
export { default as JourneysListing } from './journeys-listing.svelte';
export { default as JourneyDetail } from './journey-detail.svelte';
// Phase C's two analysis surfaces: the pre-publish dry run (mounted in the builder as a drawer)
// and the post-publish step funnel (its own page). Shared for the same reason everything else here
// is — the admin portal mounts both read-only over its own `?tenant=` loaders.
export { default as FlowDryRun } from './flow-dry-run.svelte';
export { default as FlowFunnel } from './flow-funnel.svelte';

export * from './applications.js';
// The legacy → v3 conversion. Its own module rather than part of `nodes.js` because reading a legacy
// definition and converting one are different acts that must not be confused again — twice now they
// have been, in both directions.
export * from './convert.js';
export * from './fee-summary.js';
export * from './flows.js';
export * from './insights.js';
export * from './nodes.js';
export * from './simulation.js';
export * from './steps.js';
export * from './types.js';
