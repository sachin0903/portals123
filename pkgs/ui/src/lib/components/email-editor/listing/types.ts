// The book surfaces' contract: typed props from the app's own loader, hrefs from an injected `links` builder, mutations only when the app passes `actions`. Absent ⇒ read-only, which is how each mount's permission family (`email_templates:write`, or `model:write`/`tenants:write` — `$lib/email-templates/scope`) stays app-side and this component never learns a slug. The two row shapes mirror the API's `EmailTemplateOut` (app/routers/email_templates.py) and `EmailCodeOut` (app/email/catalog.py) structurally.
import type { Enhance } from '../../book/clients/form-enhance.js';

export interface EmailTemplateRow {
	uid: string;
	code: string;
	name: string;
	description: string | null;
	version_count: number;
	current_version_number: number | null;
	current_version_status: string | null;
}

export interface EmailCodeOption {
	code: string;
	label: string;
	category: string;
	description: string;
}

/** Injected because the two mounts address templates differently — `/email-templates/{uid}/edit` against `/tenants/{uid}/email-templates/{uid}/edit` — so no string built here could be right for both. */
export interface EmailTemplatesListingLinks {
	edit: (templateUid: string) => string;
	/** The app's token-safe versions proxy (GET lists one or all, POST restores). A link rather than part of `actions`, because version history is offered read-only too. */
	versions: (templateUid: string) => string;
	/** The app's preview proxy, so a past version can be LOOKED AT and not only restored. Optional:
	 *  a mount without one keeps the list and Restore, and simply offers no View. */
	preview?: (templateUid: string) => string;
}

export interface FormActionOutcome {
	type: 'success' | 'failure' | 'redirect' | 'error';
	data?: Record<string, unknown>;
}

/** Injected because this package cannot import SvelteKit's virtuals. Delete posts with a plain `fetch` — the kebab holds no form, and `ConfirmDialog`'s `onconfirm` wants a promise — so the response is deserialised by hand, which is also how the API's 409 refusal reaches the author: it arrives as `data.error` and names the journey or code to fix. */
export type Deserialize = (text: string) => FormActionOutcome;

/** Passing the object at all IS write mode: absent ⇒ no create form, no Settings, no Delete, and the row's primary button says View. The app owns its form-action names. */
export interface EmailTemplatesListingActions {
	enhance: Enhance;
	deserialize: Deserialize;
	create: string;
	update: string;
	/** A real delete — the row and its whole version history go, and the `code` is free to use again. */
	delete: string;
	/** Admin-only, and ABSENT IS THE POINT: a firm curates the templates it was given rather than re-seeding itself, so the button does not render on its surface. */
	loadDefaults?: string;
	onmutated: () => void | Promise<void>;
	navigate: (href: string) => void | Promise<void>;
}
