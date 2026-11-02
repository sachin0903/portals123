// Pure, so the UI and the submit gate cannot disagree, and shared by both mounts. A code collision is refused in the PICKER rather than after the round trip, because the listing already holds the catalogue and the tenant's rows.
// ONLY A ROW THAT EXISTS HOLDS ITS CODE: `UNIQUE(tenant_id, code)` is the whole rule (`email_templates_service.create_template` turns the IntegrityError into `DuplicateCode`), and deleting a template releases its code, so the taken set is exactly what the listing shows.
// Codes match EXACTLY after trimming and NOT case-insensitively: `invite` and `Invite` are two distinct rows to that index, so treating them as a collision would refuse a create the API accepts.

export interface TemplateCodeRow {
	code: string;
	name: string;
}

export type CodeState = { taken: false } | { taken: true; name: string };

const FREE: CodeState = { taken: false };

export function codeState(code: string, templates: readonly TemplateCodeRow[]): CodeState {
	const wanted = code.trim();
	if (!wanted) return FREE;
	const hit = templates.find((t) => t.code.trim() === wanted);
	return hit ? { taken: true, name: hit.name } : FREE;
}

// `kind` decides the treatment: `missing` is a muted hint beside the disabled button (nothing is wrong yet, so nothing is red), `conflict` a field error — `text-destructive` + `aria-invalid`, per the design law's Field validation rule.
export interface CreateBlocker {
	message: string;
	kind: 'missing' | 'conflict';
}

export function createBlocker(input: {
	typeChosen: boolean;
	isCustom: boolean;
	code: string;
	name: string;
	state: CodeState;
}): CreateBlocker | null {
	if (!input.typeChosen) return { message: 'Choose an email type to continue.', kind: 'missing' };
	if (!input.code.trim())
		return { message: 'Enter the code this email is sent for.', kind: 'missing' };
	if (input.state.taken)
		return {
			message: `“${input.state.name}” already uses this code.`,
			kind: 'conflict'
		};
	if (!input.name.trim()) return { message: 'Give this email a name.', kind: 'missing' };
	return null;
}

// The `category` a create sends drives NOTHING at send time — whose brand an email wears is the explicit `audience` param (`PreviewEmail.audience` in
// app/routers/email_templates.py) — but the API body still requires it, so it is derived: the catalogue's value, else `notification`, which is what `CreateEmailTemplate.category` defaults to server-side.
export const DEFAULT_CATEGORY = 'notification';

export function categoryFor(catalogueCategory: string | undefined): string {
	return catalogueCategory ?? DEFAULT_CATEGORY;
}
