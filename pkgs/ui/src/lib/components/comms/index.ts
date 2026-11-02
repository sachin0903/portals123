// The outbound-email feed (Activity) — the firm's record of what it sent and what happened to it.
// See docs/comms.md.
//
// A SUBPATH export (`@graphene/ui/comms`), deliberately not on the package root: `MessageStream`,
// `MessageStatus` and `statusMeta` are far too generic for the `@graphene/ui` namespace — the same
// rule `book/listing` and `book/tasks` follow.
export { default as MessageStream } from './message-stream.svelte';
export { default as MessageDetail } from './message-detail.svelte';
export {
	statusMeta,
	neverSent,
	templateLabel,
	recipientLabel,
	humanise,
	EMPTY_COMMS_FILTERS,
	type MessageStatus,
	type CommsMessage,
	type CommsMessageEvent,
	type CommsFacets,
	type CommsFilters,
	type CommsPage
} from './describe.js';
export {
	timelineFor,
	latestEntry,
	formatRawRequest,
	type TimelineEntry
} from './message-detail.js';
