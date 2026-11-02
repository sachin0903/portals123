/** The create-form rules — the refusals the picker makes before the round trip, pinned. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
	categoryFor,
	codeState,
	createBlocker,
	DEFAULT_CATEGORY,
	type CodeState,
	type TemplateCodeRow
} from './create-rules';

const templates: TemplateCodeRow[] = [
	{ code: 'invite', name: 'Invitation' },
	{ code: 'document_sent', name: 'Document sent' }
];

describe('codeState', () => {
	// Only a row that EXISTS holds a code: `UNIQUE(tenant_id, code)` is the whole rule, and a deleted template releases its code. Case is significant, since `invite`/`Invite` are distinct rows to that index.
	it('reports a free code as available and an existing template’s as taken, naming it', () => {
		expect(codeState('task_notice', templates)).toEqual({ taken: false });
		expect(codeState('invite', templates)).toEqual({ taken: true, name: 'Invitation' });
	});

	it('frees the code of a template that is no longer listed — nothing else holds it', () => {
		// The listing IS the taken set: delete the row and the same code creates cleanly, which is what the API's `DELETE` releases.
		const afterDelete = templates.filter((t) => t.code !== 'invite');
		expect(codeState('invite', afterDelete)).toEqual({ taken: false });
		expect(codeState('invite', [])).toEqual({ taken: false });
	});

	it('trims whitespace on both sides, but does not treat a different CASE as a collision', () => {
		expect(codeState('  invite  ', templates)).toMatchObject({ taken: true });
		expect(codeState('invite', [{ code: ' invite ', name: 'Invitation' }])).toMatchObject({
			taken: true
		});
		expect(codeState('Invite', templates)).toEqual({ taken: false });
	});

	it('treats an empty or blank code as free — that is a "not finished", not a collision', () => {
		expect(codeState('', templates)).toEqual({ taken: false });
		expect(codeState('   ', templates)).toEqual({ taken: false });
	});
});

describe('createBlocker', () => {
	const free: CodeState = { taken: false };
	const blocker = (over: Partial<Parameters<typeof createBlocker>[0]>) =>
		createBlocker({ typeChosen: true, isCustom: true, code: '', name: '', state: free, ...over });

	it('asks for the type, then the code, then the name, in that order', () => {
		expect(blocker({ typeChosen: false, isCustom: false })).toEqual({
			message: 'Choose an email type to continue.',
			kind: 'missing'
		});
		const noCode = blocker({});
		expect(noCode?.kind).toBe('missing');
		expect(noCode?.message).toMatch(/code/i);
		expect(blocker({ code: 'renewal', name: '  ' })).toEqual({
			message: 'Give this email a name.',
			kind: 'missing'
		});
	});

	it('reports a collision as a conflict, naming the holder, ahead of a missing name', () => {
		// Code beats name as the harder refusal (a name can be typed, a taken code can't), and naming the holder is what makes it fixable — that row is the one to rename, re-point or delete.
		const hit = blocker({
			code: 'invite',
			name: '',
			state: { taken: true, name: 'Invitation' }
		});
		expect(hit?.kind).toBe('conflict');
		expect(hit?.message).toContain('Invitation');
		expect(hit?.message).not.toMatch(/restore/i);
	});

	it('clears once the form is complete and the code is free', () => {
		expect(blocker({ isCustom: false, code: 'task_notice', name: 'Task notice' })).toBeNull();
	});
});

describe('categoryFor', () => {
	it('uses the catalogue’s own category, falling back to the API’s documented default', () => {
		expect(categoryFor('transactional')).toBe('transactional');
		expect(categoryFor(undefined)).toBe('notification');
		expect(DEFAULT_CATEGORY).toBe('notification');
	});
});

describe('the picker only offers what can be chosen', () => {
	// Every built-in type listed and DISABLED once taken is six dead rows in the normal all-six-added tenant, above the one option that works; tested here because the component derives the offered set from `codeState`.
	const listing = readFileSync(join(import.meta.dirname, 'email-templates-listing.svelte'), 'utf8');

	it('drops a type an existing template holds, rather than listing it disabled', () => {
		expect(listing).toContain('const offeredTypes');
		expect(listing).toMatch(/catalog\.filter\(\(c\) => !catalogState\.get\(c\.code\)\?\.taken\)/);
		expect(listing).toMatch(/\{#each offeredTypes as c \(c\.code\)\}/);
		// No greyed-out option survives, so no per-option note is rendered either.
		expect(listing).not.toContain('takenNote');
		expect(listing).not.toMatch(/<Select\.Item value=\{c\.code\} disabled/);
	});

	it('hides the whole control when there is nothing left to offer', () => {
		expect(listing).toContain('const noTypesLeft');
		expect(listing).toMatch(/\{#if hasCatalog && !noTypesLeft\}/);
	});

	it('still lets the form submit with no picker on screen', () => {
		// With the picker gone, code can only come from free text, so that state must count as custom — otherwise the button is dead with nothing on screen to explain why.
		expect(listing).toMatch(/isCustom = \$derived\([^)]*noTypesLeft\)/);
		expect(
			createBlocker({
				typeChosen: true,
				isCustom: true,
				code: 'renewal_notice',
				name: 'Renewal notice',
				state: { taken: false }
			})
		).toBeNull();
	});
});
