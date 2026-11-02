/** The scope vocabulary must be exactly what `build_context` can produce — offering a field the
 *  consumer cannot resolve is how an author ends up with a blank in a client's email. Reads the Python
 *  source, so a field added or renamed there fails here rather than drifting quietly. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ALWAYS, BY_SUBJECT, KNOWN_PATHS, fieldsFor, grouped, type SubjectKind } from './scopes';

const source = readFileSync(
	join(
		fileURLToPath(new URL('../../../../../../', import.meta.url)),
		'apps/api/app/notifications/context.py'
	),
	'utf8'
);

/** `SCOPE_FIELDS` as {subject or 'None': paths}. */
const pythonScopes = (): Record<string, string[]> => {
	const body = source.slice(
		source.indexOf('SCOPE_FIELDS: dict[str | None, tuple[str, ...]] = {'),
		source.indexOf('\n#: Flattened')
	);
	const out: Record<string, string[]> = {};
	for (const [, key, paths] of body.matchAll(/(None|"(?:\w+)"):\s*\(([\s\S]*?)\),/g)) {
		out[key.replace(/"/g, '')] = [...paths.matchAll(/"([\w.]+)"/g)].map((m) => m[1]);
	}
	return out;
};

describe('parity with build_context', () => {
	it('parses the Python, so the assertions below cannot pass vacuously', () => {
		const scopes = pythonScopes();
		expect(Object.keys(scopes).length).toBeGreaterThan(3);
		expect(scopes.None?.length).toBeGreaterThan(2);
	});

	it('offers exactly the always-available fields', () => {
		expect(ALWAYS.map((f) => f.path).sort()).toEqual([...(pythonScopes().None ?? [])].sort());
	});

	it('claims exactly the subject kinds the consumer can build a context for', () => {
		// BOTH directions. The per-kind check below iterates OUR keys, so a kind added to Python's
		// `SCOPE_FIELDS` and not here is never asked about — it would simply never appear in the
		// editor's picker, and the suite that exists to catch that would pass. Python keys the
		// always-available set under `None`, which is `ALWAYS` on this side, so it is not a subject.
		const theirs = Object.keys(pythonScopes()).filter((k) => k !== 'None');
		expect(Object.keys(BY_SUBJECT).sort()).toEqual(theirs.sort());
	});

	it('offers exactly each subject kind’s fields', () => {
		const scopes = pythonScopes();
		for (const kind of Object.keys(BY_SUBJECT) as SubjectKind[]) {
			// The Python list per subject includes only the subject's own fields plus the person ones;
			// the TS side adds the always-available set separately, so compare without it.
			const always = new Set(ALWAYS.map((f) => f.path));
			const ours = BY_SUBJECT[kind].map((f) => f.path).filter((p) => !always.has(p));
			expect(ours.sort(), kind).toEqual([...(scopes[kind] ?? [])].sort());
		}
	});

	it('knows no path the consumer cannot resolve', () => {
		const all = new Set(Object.values(pythonScopes()).flat());
		for (const path of KNOWN_PATHS) expect(all, path).toContain(path);
	});
});

describe('fieldsFor', () => {
	it('is the constants alone when the email claims no subject', () => {
		expect(fieldsFor([]).map((f) => f.path)).toEqual(ALWAYS.map((f) => f.path));
	});

	it('adds a subject’s fields, and de-dupes the person ones two subjects share', () => {
		const both = fieldsFor(['account', 'document']).map((f) => f.path);
		expect(both).toContain('account.value');
		expect(both).toContain('document.name');
		expect(both.filter((p) => p === 'client.name')).toHaveLength(1);
	});

	it('gives every field words an author can read', () => {
		// The path is the machine's name for it; a row with no label is a row nobody can choose.
		for (const field of fieldsFor(['contact', 'account', 'task', 'document'])) {
			expect(field.label.trim().length, field.path).toBeGreaterThan(0);
		}
	});
});

describe('grouped', () => {
	it('keeps each scope together, in the order the fields came in', () => {
		const groups = grouped(fieldsFor(['account'])).map((g) => g.group);
		expect(groups).toEqual([...new Set(groups)]);
		expect(groups).toContain('account');
		expect(groups).toContain('firm');
	});
});

/** The subjects are set at creation AND changeable later — an author revises what an email is about
 *  as they write it, and a one-and-done choice at creation would strand them. */
describe('where the subjects can be changed', () => {
	const read = (path: string) =>
		readFileSync(new URL(path, import.meta.url), 'utf8').replace(/<!--[\s\S]*?-->/g, '');
	const panel = read('./fields-reference-dialog.svelte');
	const page = read('./email-template-editor-page.svelte');
	const listing = read('./listing/email-templates-listing.svelte');

	it('is asked at creation', () => {
		expect(listing).toContain('What is this email about?');
		expect(listing).toMatch(/name="subjects"/);
	});

	it('is editable in the context panel, where its effect is visible', () => {
		// The panel reads context-then-choices, so the choices are stated as what WIDENS the context.
		expect(panel).toContain('Always given');
		expect(panel).toContain('Also about');
		expect(panel).toMatch(/onsubjects\?\.\(/);
		// Read-only without the callback, so a mount that cannot save shows them and edits nothing.
		expect(panel).toMatch(/disabled=\{!onsubjects\}/);
	});

	it('counts a change as unsaved work, and withholds it from a reader', () => {
		expect(page).toMatch(/onsubjects=\{canManage/);
		expect(page).toMatch(/subjects = next;[\s\S]{0,40}dirty = true;/);
	});

	it('is re-posted with the body, because the save REPLACES the schema', () => {
		// Without this the claim is lost the first time anyone edits the email.
		expect(page).toMatch(/name="subjects"/);
		expect(page).toMatch(/subjects\.length \? JSON\.stringify\(subjects\) : ''/);
	});
});
