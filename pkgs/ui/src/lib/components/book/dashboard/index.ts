export { default as ClientsTable } from './clients-table.svelte';
export { default as ActionQueue } from './action-queue.svelte';
export { default as FirmDashboard } from './firm-dashboard.svelte';
export {
	PLATFORM_DASH_LINKS,
	prefixedDashLinks,
	type Dash,
	type DashClientRow,
	type DashLinks,
	type FirmOverview
} from './dash.js';
export {
	BOOK_COLUMNS,
	STATUS_OPTIONS,
	STATUS_VARIANT,
	filterBook,
	statusOf,
	type BookFilters,
	type BookRow,
	type ClientStatus
} from './book';
export {
	byNeed,
	DEFAULT_RAIL_LINKS,
	journeyItems,
	railItems,
	reviewItems,
	type RailItem,
	type RailKind,
	type RailLinks
} from './action-queue';
export { notesClient, type NotesClient, type StaffNote } from './notes.js';
