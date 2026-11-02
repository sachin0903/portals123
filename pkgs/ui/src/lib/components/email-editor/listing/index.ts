// Shared listing mounted by platform (`/email-templates`) and admin (`/tenants/[uid]/email-templates`) over their own loaders; re-exported from the `@graphene/ui/email-editor` subpath, not the root (grapesjs lives here).
export { default as EmailTemplatesListing } from './email-templates-listing.svelte';
export type {
	EmailTemplateRow,
	EmailCodeOption,
	EmailTemplatesListingLinks,
	EmailTemplatesListingActions,
	Deserialize,
	FormActionOutcome
} from './types.js';

// Re-exported so a loader reaches the same answer the table shows, not a re-derived one.
export {
	emailTemplateState,
	type EmailTemplateState,
	type EmailTemplateStateInput
} from './template-state.js';
export {
	categoryFor,
	codeState,
	createBlocker,
	DEFAULT_CATEGORY,
	type CodeState,
	type CreateBlocker,
	type TemplateCodeRow
} from './create-rules.js';
