/**
 * A form that SUBMITS a file declares `enctype="multipart/form-data"`. Scanned across all three
 * portals, on the source.
 *
 * WHY A STATIC SCAN. Nothing else can see this. The markup type-checks, the component renders, the
 * chosen files appear in the list on screen — and `use:enhance` then serialises the body as
 * URLSearchParams, so a File becomes the seven-character string "[object File]" and the action
 * reports "choose a file" about files the user watched it accept. In dev the same omission THROWS
 * inside the submit handler before the fetch, so the button produces no request, no server log and
 * no message on the page. The two ways it fails are "it says I chose nothing" and "nothing
 * happens", and neither points at an attribute that is missing rather than wrong.
 *
 * Not hypothetical: it is how the look-through importer shipped.
 *
 * SCOPE IS THE WHOLE RULE. Only forms that submit THROUGH SVELTEKIT are in it — `use:enhance`, or
 * a native `method="post"` with no handler of its own. The messaging composers each carry a file
 * input inside a `<form onsubmit={send}>` that builds its own FormData and fetches; native
 * serialisation never runs there, so `enctype` would be decoration, and demanding it would teach
 * the next reader the wrong reason for it.
 *
 * It lives in the admin app and reads the sibling portals' trees for the reason
 * `route-addressing.test.ts` does: the rule is one rule, and three copies are three chances to fix
 * one portal and leave the others. The "scans what it claims to scan" case is the guard on the
 * guard — a rearranged workspace must fail loudly rather than pass vacuously.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { PORTALS, sources } from './route-addressing';

type Form = { tag: string; body: string };

/**
 * Every `<form>` in a snippet, as its opening tag plus the markup up to `</form>`.
 *
 * Positional, from the match index rather than `indexOf(tag)`: the two message composers in one
 * file have byte-identical opening tags, and a name-based lookup reads the first one's body twice.
 */
const forms = (source: string): Form[] =>
	[...source.matchAll(/<form\b[^>]*>/g)].map((match) => {
		const start = match.index + match[0].length;
		const end = source.indexOf('</form>', start);
		return { tag: match[0], body: source.slice(start, end === -1 ? undefined : end) };
	});

/** Does SvelteKit own this form's submission — `use:enhance`, or a native POST with no handler? */
const submitsThroughKit = ({ tag }: Form): boolean =>
	/\buse:enhance\b/.test(tag) || (/\bmethod=["']post["']/i.test(tag) && !/\bonsubmit=/.test(tag));

/**
 * Does the form carry a file?
 *
 * Two spellings, because the input can be the caller's own or the shared dropzone's. A
 * `FileDropzone` joins submission only when it is given a `name`; without one it is component
 * state the caller posts itself, so an unnamed one deliberately does not match.
 */
const carriesAFile = ({ body }: Form): boolean =>
	/<input\b[^>]*type=["']file["']/.test(body) || /<FileDropzone\b[^>]*\bname=/.test(body);

const svelteSources = (): string[] =>
	PORTALS.flatMap((app) => sources(app).filter((path) => path.endsWith('.svelte')));

describe('a form that submits a file says so', () => {
	it('declares an enctype on every file-carrying form SvelteKit submits', () => {
		const offenders = svelteSources().filter((path) =>
			forms(readFileSync(path, 'utf8')).some(
				(form) => submitsThroughKit(form) && carriesAFile(form) && !/\benctype=/.test(form.tag)
			)
		);
		expect(offenders).toEqual([]);
	});

	it('scans the portals it claims to scan', () => {
		expect(svelteSources().length).toBeGreaterThan(100);
	});

	it('reads a file input, a named dropzone and neither', () => {
		const [bare] = forms('<form method="POST"><input type="file" name="logo" /></form>');
		const [named] = forms('<form method="POST"><FileDropzone bind:files name="files" /></form>');
		const [unnamed] = forms('<form method="POST"><FileDropzone bind:files multiple /></form>');
		expect(carriesAFile(bare!)).toBe(true);
		expect(carriesAFile(named!)).toBe(true);
		expect(carriesAFile(unnamed!)).toBe(false);
	});

	it('leaves a form that posts its own body alone', () => {
		const [own] = forms('<form onsubmit={send}><input type="file" /></form>');
		const [enhanced] = forms('<form method="POST" use:enhance={go}><input type="file" /></form>');
		expect(submitsThroughKit(own!)).toBe(false);
		expect(submitsThroughKit(enhanced!)).toBe(true);
	});

	it('takes each form its own body, even when two opening tags are identical', () => {
		const pair = forms(
			'<form onsubmit={send}><input type="file" /></form><form onsubmit={send}>nothing</form>'
		);
		expect(pair.map(carriesAFile)).toEqual([true, false]);
	});
});
