// THE CANVAS BRIDGE IS PINNED TO ITS TWO ENDS. The GrapesJS canvas frame is same-origin and src-less, so
// script injected into it runs in the portal's own origin under the author's session — and the frame's
// listener carries `gjs-email-insert-field`'s markup straight to `document.execCommand('insertHTML')`,
// deliberately unsanitised, because THE PARENT OWNS THE MARKUP (a second copy of `fieldSpanHtml` in an
// unimportable static asset is exactly how the paths would drift). The source pin is therefore the whole of
// the defence: a foreign page can hold a WindowProxy to the frame — no app here sets `frame-ancestors` or
// `X-Frame-Options` — but it can never become the frame's `window.parent`, nor the editor's `contentWindow`.
// Scanned as source text because the frame script is a classic asset nothing here can import or execute.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// fileURLToPath, not `.pathname` — on Windows the latter yields `/C:/…`, which `join` mangles into `C:\C:\…`.
const REPO_ROOT = fileURLToPath(new URL('../../../../../../', import.meta.url));
const read = (p: string): string => readFileSync(join(REPO_ROOT, p), 'utf8');

const COPIES = [
	'apps/platform/static/gjs-email-jinja-hl.js',
	'apps/admin/static/gjs-email-jinja-hl.js'
];
const EDITOR_PATH = 'pkgs/ui/src/lib/components/email-editor/email-editor.svelte';
const EDITOR = read(EDITOR_PATH);

/** Every `postMessage(` call in a file, paren-counted so a multi-line argument list stays one call. */
function postMessageCalls(source: string): string[] {
	const calls: string[] = [];
	for (const match of source.matchAll(/postMessage\(/g)) {
		const open = (match.index ?? 0) + match[0].length - 1;
		let depth = 0;
		for (let i = open; i < source.length; i++) {
			if (source[i] === '(') depth++;
			else if (source[i] === ')' && --depth === 0) {
				calls.push(source.slice(match.index, i + 1));
				break;
			}
		}
	}
	return calls;
}

describe('the canvas frame’s message listener', () => {
	it('reads both copies of the frame script, so the assertions below cannot pass vacuously', () => {
		for (const copy of COPIES) expect(read(copy), copy).toContain("addEventListener('message'");
	});

	it('is byte-identical in both apps, so the pin cannot protect only one portal', () => {
		const [first, ...rest] = COPIES.map((copy) => ({ copy, text: read(copy) }));
		for (const other of rest)
			expect(other.text, `${other.copy} differs from ${first?.copy}`).toBe(first?.text);
	});

	it('answers only its own editor shell, and pins the source before anything else', () => {
		for (const copy of COPIES) {
			const source = read(copy);
			const body = source.slice(source.indexOf("addEventListener('message'"));
			const sourcePin = body.indexOf('e.source !== window.parent');
			const originPin = body.indexOf('e.origin !== window.location.origin');
			const firstUse = body.indexOf('e.data');
			expect(sourcePin, `${copy} does not pin the message source`).toBeGreaterThan(-1);
			expect(originPin, `${copy} does not pin the message origin`).toBeGreaterThan(-1);
			// The source pin first: it is the one that holds wherever a src-less frame reports an origin
			// of its own, and neither may sit after the first read of a stranger's payload.
			expect(sourcePin, `${copy} pins the origin before the source`).toBeLessThan(originPin);
			expect(originPin, `${copy} reads e.data before pinning the sender`).toBeLessThan(firstUse);
		}
	});

	it('names its parent when it answers, never every window at once', () => {
		for (const copy of COPIES)
			for (const call of postMessageCalls(read(copy))) {
				expect(call, `${copy} posts to a wildcard target`).not.toContain("'*'");
				expect(call, `${copy} posts without naming an origin`).toContain('window.location.origin');
			}
	});
});

describe('the editor shell’s half of the bridge', () => {
	it('reads the component, so the assertions below cannot pass vacuously', () => {
		expect(EDITOR).toContain('const onFrameMessage');
	});

	it('answers only its own canvas frame, and pins the source before anything else', () => {
		const handler = EDITOR.slice(
			EDITOR.indexOf('const onFrameMessage'),
			EDITOR.indexOf("window.addEventListener('message', onFrameMessage)")
		);
		const sourcePin = handler.indexOf('event.source !== frameWindow()');
		const originPin = handler.indexOf('event.origin !== window.location.origin');
		const firstUse = handler.indexOf('event.data');
		expect(sourcePin, 'the shell does not pin the message source').toBeGreaterThan(-1);
		expect(sourcePin).toBeLessThan(originPin);
		expect(originPin).toBeLessThan(firstUse);
	});

	it('names the frame’s origin when it posts, never every window at once', () => {
		for (const call of postMessageCalls(EDITOR)) {
			expect(call, 'the shell posts to a wildcard target').not.toContain("'*'");
			expect(call, 'the shell posts without naming an origin').toContain('window.location.origin');
		}
	});

	it('checks the shape of the component id the frame answers with', () => {
		// It is interpolated into a selector (`find('#…')`), and a GrapesJS component id is a bare word.
		const fn = EDITOR.split('function appendField')[1]?.split('\n\t}')[0] ?? '';
		expect(fn).toMatch(/\/\^\[A-Za-z0-9_-\]\+\$\/\.test\(intoId\)/);
		expect(fn).not.toMatch(/find\?\.\(`#\$\{intoId\}`\)/);
	});
});
