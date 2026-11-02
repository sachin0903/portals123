// The shared tasks vocabulary + surfaces (docs/tasks.md) for the portal waterfall: the platform
// portal's tasks inbox and the admin portal's per-tenant view render the same book components.
//
// Deliberately NOT re-exported from the package root: `TaskRow`/`isOpen`/`dueLabel` are far too
// generic for the `@graphene/ui` namespace (and `TaskRow` would collide with the documents book's
// own row type). Consumers import the subpath `@graphene/ui/book/tasks` (see pkgs/ui package.json
// `exports`), exactly as `@graphene/ui/book/listing` does.
export {
	ASSIGNEE_KIND_LABELS,
	byUrgency,
	CATEGORY_LABELS,
	dueLabel,
	isOpen,
	isOverdue,
	TASK_SCOPE_LABELS,
	todayIso,
	type TaskDisplayRow,
	type TaskRow,
	type TaskScope
} from './tasks.js';

// The extracted page cores: data as typed props, hrefs from the injected `links`, mutations only
// when the app passes `actions` (its form-action names + its own `enhance` from `$app/forms` —
// pkgs/ui cannot import SvelteKit virtuals). Actions absent ⇒ the surfaces render read-only.
export { default as TasksListing } from './tasks-listing.svelte';
export type { TasksListingActions } from './tasks-listing.svelte';
export { default as TaskDetail } from './task-detail.svelte';
export type { TaskDetailActions } from './task-detail.svelte';

// The data shapes the components read (structural — an app's richer API types are assignable).
export type {
	AssigneeOption,
	TaskActivity,
	TaskClientOption,
	TaskDetailLinks,
	TaskMemberOption,
	TaskRoleOption,
	TasksListingLinks
} from './types.js';
