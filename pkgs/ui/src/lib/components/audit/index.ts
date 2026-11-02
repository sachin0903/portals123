// The action-event stream, read for humans (audit.actions / GET /admin/audit/actions) — the
// master-detail EventStream (list of actor-led rows + a detail pane for the selected event).
// Mounted in the admin Events page (cross-firm + per-tenant) and the platform Events page (a firm's
// own slice). The row markup lives in `event-stream.svelte`'s own cell snippet (it was a separate
// `event-row.svelte` until the table became a sortable `DataTable`, which renders per COLUMN and
// leaves no seam for a whole-row component), the detail pane `event-detail.svelte`, the pure read
// logic `event-detail.ts` (unit-tested).
export {
	default as EventStream,
	EMPTY_ACTION_LOG_FILTERS,
	type ActionLogFilters,
	type ActionLogTenant
} from './event-stream.svelte';
// The filter↔API-query mapping behind those filters, PURE — read by every Events loader and every
// same-origin "Load more" proxy, so the exclusive-`until` and tri-state-`proxied` rules live once
// instead of in eight copies that answer 200 with a wrong page when one of them drifts.
export {
	actionLogFiltersFromSearchParams,
	actionLogBrowserQuery,
	actionLogQuery,
	actionLogUntilBound,
	type ActionLogPage
} from './action-log-query.js';
// The compact, read-only event feed — the same humanising vocabulary as EventStream, a single line
// per event, sized for a sidebar card / rail / record tab. What the documents.audit_log activity
// feeds became once they draw from the action stream.
export { default as EventFeed } from './event-feed.svelte';
export { groupEvents, type GroupedActionEvent } from './event-feed.js';
// The Events toolbar's searchable-filter derivations — the actor suggestions are shared with the
// admin audit-log's own toolbar; the entity-kind tag picker filters by the DOCUMENT/CLIENT/… chips.
export {
	actorSuggestions,
	entityTagSuggestions,
	matchesTags,
	type ActorLike,
	type TaggedLike
} from './event-stream-filters.js';
// The SAFE JSON highlighter, shared with the admin audit-log detail pane (one tokenizer, no @html).
export { highlightJson, type HighlightSeg } from './highlight-json.js';
// Display formatters that tame big/markup column values in the detail panes (a branding theme jsonb,
// a GrapesJS email body) — shared so the admin audit-log detail formats the same way as EventStream.
export { capText, previewValue, shortenForJson, omitEmpty } from './format-value.js';
export {
	describeActionEvent,
	actorLabel,
	actorLedSentence,
	deslug,
	entityKindLabel,
	eventCategory,
	methodLabel,
	methodRoute,
	permissionsDiff,
	statusLabel,
	statusTone,
	timestampParts,
	type ActionCategory,
	type ActionEntity,
	type ActionEvent,
	type TimestampParts
} from './describe.js';
