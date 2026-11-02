// The editor page's AUDIENCE control decides whose branding — and, on a test send, which sender identity — an email renders with; it was live server-side and unreachable from the browser, so every preview/test send fell back to the API's `client` default even for a `task_notice`, which really goes out staff-branded.
// Pinned as two effects: the SSR-rendered segment actually selected for a code (a `$derived`, not effect-seeded, so the server render is already right) and that the live-content payload builder carries `audience` and both the preview dialog and test send read that one builder.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import EmailTemplateEditorPage from './email-template-editor-page.svelte';

const SOURCE = readFileSync(
	join(import.meta.dirname, 'email-template-editor-page.svelte'),
	'utf8'
).replace(/<!--[\s\S]*?-->/g, ''); // comments legitimately discuss `audience` at length

const body = (code: string) =>
	render(EmailTemplateEditorPage, {
		props: {
			template: { uid: 'eml_abc', code, name: 'Task notice' },
			version: null,
			canManage: true,
			documentTitle: 'Edit email',
			backHref: '/email-templates',
			backLabel: 'Email templates',
			previewUrl: '/email-templates/eml_abc/preview',
			sendTestUrl: '/email-templates/eml_abc/send-test',
			// The app injects these; SSR never invokes `enhance` or `goto`, and `beforeNavigate` is
			// called at init.
			enhance: (() => ({ destroy: () => {} })) as never,
			beforeNavigate: () => {},
			goto: async () => undefined
		}
	}).body;

describe('the audience is derived, not chosen', () => {
	// A Client/Staff toggle here did nothing visible (the code already gives the right answer, and most firms' portals share branding anyway) — its removal must not silently reappear, though the value itself still travels in both requests below.
	it('offers no audience control', () => {
		const html = body('document_sent');
		expect(html).not.toContain('Preview as');
		// Named absences, not "no `role=tab`" — that would also fail on the editor's own Desktop/Mobile `Segmented` toggle; what must not come back is a way to CHOOSE the audience, so that's what's asserted.
		expect(html).not.toMatch(/aria-label="Client"|>\s*Client\s*<\/button>/);
		expect(html).not.toMatch(/>\s*Staff\s*<\/button>/);
	});

	it('is not a form field — it can never be posted as template content', () => {
		// An `audience` input would make every save carry a value the template has no column for, and misread as "this template is for staff".
		expect(body('task_notice')).not.toMatch(/name="audience"/);
	});
});

describe('the audience reaches both requests', () => {
	it('is part of the single live-content payload builder, read by BOTH callers', () => {
		// One shared builder, not two payloads — a preview and a test send of the same canvas must not disagree about audience; `liveContent()` is typed `PreviewPayload` so the compiler enforces the field.
		expect(SOURCE).toMatch(/function liveContent\(\): PreviewPayload/);
		expect(SOURCE).toMatch(/return \{[^}]*audience[^}]*\};/);
		expect(SOURCE).toMatch(/send\(liveContent\(\)\)/);
		expect(SOURCE).toMatch(/content=\{liveContent\}/);
	});

	it('derives the default from the template code rather than hardcoding one', () => {
		// No inline mapping in the markup — the semantics live in the tested pure function.
		expect(SOURCE).toMatch(/defaultAudienceFor\(template\.code\)/);
		expect(SOURCE).not.toMatch(/'task_notice'/);
	});
});

// A mistyped subject token renders as nothing, silently, in the one line every recipient reads; asserted on source because the merge menu is portalled and only exists once open.

/** The subject and the preview line are the only places an author would otherwise type Jinja by hand,
 *  and a mis-typed token renders BLANK. They get the field menu through the RAIL's one pill rather than
 *  a button each: the same list, routed to whichever line the author was last in. */
describe('the head lines can insert a field', () => {
	it('adds no button of its own — one affordance, not three', () => {
		expect(body('invite')).not.toContain('aria-label="Insert a field"');
	});

	it('routes the rail’s insert into the head line the author was last in', () => {
		expect(SOURCE).toMatch(/interceptInsert=\{\(token\) => \{/);
		expect(SOURCE).toMatch(/insertIntoHead\(headCaret\.field, token\)/);
		// And falls through to the canvas when it was none of them.
		expect(SOURCE).toMatch(/if \(!headCaret \|\| !canManage\) return false;/);
	});

	it('forgets the head caret once the author works in the canvas', () => {
		// Otherwise every later insert lands in the subject, because a caret is only ever remembered.
		expect(SOURCE).toMatch(/oncanvasfocus=\{\(\) => \(headCaret = null\)\}/);
	});

	it('inserts through the pure caret helper rather than string-poking the value', () => {
		expect(SOURCE).toMatch(/insertAtCaret\(current, token, at\?\.start, at\?\.end\)/);
	});

	it('remembers the caret, because the rail takes focus off the input', () => {
		for (const event of ['onselect=', 'onkeyup=', 'onclick=']) {
			const lines = SOURCE.split(/\r?\n/).filter(
				(l) => l.includes(event) && l.includes('rememberCaret(')
			);
			expect(lines, event).toHaveLength(2);
		}
	});
});

describe('a publish reports the tokens nothing supplies', () => {
	// THE DEFECT: publish renders every part and only asked "did it raise", so a hand-typed token
	// nothing fills published clean and rendered blank on every send. The list existed but was
	// reachable only through the Preview dialog, so a template published without opening Preview
	// shipped its blanks unremarked.
	it('reads the warning off a SUCCESSFUL publish only', () => {
		expect(SOURCE).toMatch(/form\?\.action === 'publish' && form\.ok/);
		expect(SOURCE).toMatch(/form\.unsourced \?\? \[\]/);
	});

	it('renders it as a warning, not an error — the publish succeeded', () => {
		const banner = SOURCE.slice(SOURCE.indexOf('{#if publishWarnings.length}'));
		expect(banner).not.toMatch(/^\s*<Alert variant="destructive"/m);
		expect(banner).toContain('publishWarnings.join');
	});

	it('says what it checked rather than pronouncing the template clean', () => {
		// The list cannot see a token behind an `{% if %}`, nor a present-but-null value, so wording
		// that implied "no problems" would be a false reassurance. Same sentence as preview-dialog.
		expect(SOURCE).toContain('had no sample value and rendered empty');
		expect(SOURCE).not.toMatch(/no (problems|issues)|looks good|all (fields|tokens) (are )?fine/i);
	});
});

describe('a setting saved from code is not one edit behind', () => {
	/** `doSave()` is called the moment a setting changes — the context panel's "Save context" does
	 *  exactly that — and `requestSubmit` gathers the form SYNCHRONOUSLY, before Svelte has flushed the
	 *  new value into the hidden input. So the claim posted was the PREVIOUS one, for ever one edit
	 *  behind, while the panel reported success. Read from state at submit time instead, which is the
	 *  idiom the html/css/source fields already use for the same reason. */
	const submit =
		/use:enhanceForm=\{\(\{ formData \}\) => \{([\s\S]*?)saving = true;/.exec(SOURCE)?.[1] ?? '';

	it('reads the claim from state, not from the DOM', () => {
		expect(submit).not.toBe('');
		expect(submit).toContain("formData.set('subjects'");
	});

	it('keeps an empty claim posting NO key, so claims-none stays absent', () => {
		// The hidden input's own rule: '' means the app action posts no key and the schema never
		// acquires an empty one.
		expect(submit).toMatch(
			/formData\.set\('subjects', subjects\.length \? JSON\.stringify\(subjects\) : ''\)/
		);
	});
});
