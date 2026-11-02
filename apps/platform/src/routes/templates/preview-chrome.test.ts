import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Which PdfViewer mounts wear the viewer's chrome.
 *
 * `PdfViewer` owns a find bar and a zoom pill, and they are right on a reading surface and wrong on
 * a thumbnail — where they float over the very page they are covering. The distinction lives in
 * per-mount props (`searchable` / `zoomable`), NOT in the component, because the same component
 * legitimately serves both; a change made in the component to tidy one surface silently strips the
 * other, which is how the chrome bled into the template preview in the first place.
 *
 * A source scan because nothing else can see it: these are props on a mount, invisible to
 * svelte-check and to any test of the viewer itself.
 */
const ROUTES = join(import.meta.dirname);
const UI = join(
	import.meta.dirname,
	'..',
	'..',
	'..',
	'..',
	'..',
	'pkgs',
	'ui',
	'src',
	'lib',
	'components'
);

const templateOverview = readFileSync(join(ROUTES, '[uid]', '+page.svelte'), 'utf8');
const documentViewer = readFileSync(
	join(UI, 'ui', 'document-viewer', 'document-viewer.svelte'),
	'utf8'
);
const pdfViewer = readFileSync(join(UI, 'ui', 'pdf-viewer', 'pdf-viewer.svelte'), 'utf8');

/** The attributes of the first `<PdfViewer …/>` tag in a file. */
function mount(code: string): string {
	const m = /<PdfViewer\b([\s\S]*?)\/>/.exec(code);
	expect(m, 'no PdfViewer mount found').not.toBeNull();
	return m![1];
}

describe('the template overview preview is a thumbnail, not a reader', () => {
	it('drops the find bar and the zoom pill', () => {
		// A 26rem box: the controls cover the page they sit on, and the answer to "I need to look
		// closer" is to open the document, not to zoom a preview of it.
		const props = mount(templateOverview);
		expect(props).toMatch(/searchable=\{false\}/);
		expect(props).toMatch(/zoomable=\{false\}/);
	});
});

describe('the documents viewer keeps its chrome', () => {
	it('never opts out of search or zoom', () => {
		// It IS the reading surface. Search is ours rather than the browser's because pages render
		// lazily — the browser's find would report hits only from the two or three pages that happen
		// to be painted — and zoom is ours because the browser's scales the canvas and blurs the page
		// it was asked to enlarge. Withholding either here would be a real loss of function, not
		// tidier chrome.
		const props = mount(documentViewer);
		expect(props).not.toMatch(/searchable=\{false\}/);
		expect(props).not.toMatch(/zoomable=\{false\}/);
	});

	it('still asks for the expand affordance it owns', () => {
		expect(mount(documentViewer)).toMatch(/\{expanded\}[\s\S]*?\{onexpand\}/);
	});
});

describe('the padding follows the chrome', () => {
	it('reserves the pill gap only when a pill will be there', () => {
		// The vertical padding exists to keep the floating pills off the first and last page. Held at
		// a constant `pt-16 pb-16` it was 8rem of dead space on a pane with no pills — nearly a third
		// of the 26rem thumbnail, which reads as a broken gap above the document rather than as
		// room being reserved for something.
		expect(pdfViewer).toMatch(
			/px-3 \{searchable \? 'pt-16' : 'pt-3'\} \{zoomable \? 'pb-16' : 'pb-3'\}/
		);
	});

	it('keeps the horizontal padding constant, because the fit width measures it', () => {
		// `HOST_PADDING` is the px-3 either side; a conditional there would silently mis-fit the page.
		expect(pdfViewer).toMatch(/const HOST_PADDING = 24;/);
		expect(pdfViewer).toMatch(/class="min-h-full w-full px-3 /);
	});
});

describe('no other surface was affected', () => {
	// The change lives in a SHARED component, so "it looks right on the page I was staring at" is
	// not evidence. This walks the repo and asserts the blast radius directly: exactly one mount
	// opts out of the chrome, and it is the thumbnail.
	const ROOT = join(import.meta.dirname, '..', '..', '..', '..', '..');
	const SKIP = new Set(['node_modules', '.svelte-kit', 'dist', 'build', '.git']);

	function svelteFiles(dir: string, out: string[] = []): string[] {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			if (SKIP.has(entry.name)) continue;
			const full = join(dir, entry.name);
			if (entry.isDirectory()) svelteFiles(full, out);
			else if (entry.name.endsWith('.svelte')) out.push(full);
		}
		return out;
	}

	/** Every `<PdfViewer …/>` mount in the product, as [file, attributes]. */
	const mounts = ['apps', 'pkgs'].flatMap((top) =>
		svelteFiles(join(ROOT, top)).flatMap((file) => {
			const code = readFileSync(file, 'utf8');
			return [...code.matchAll(/<PdfViewer\b([\s\S]*?)\/>/g)].map(
				// split/join rather than a regex: a literal backslash class in a path replace is one
				// escaping slip away from matching nothing and silently reporting zero mounts.
				(m) =>
					[
						file
							.split('\\')
							.join('/')
							.slice(ROOT.length + 1),
						m[1]
					] as const
			);
		})
	);

	it('finds every mount, so the count itself is the tripwire', () => {
		// A new mount added later fails here and has to make a deliberate choice about its chrome
		// rather than inheriting whichever default happens to be in force.
		expect(mounts.map(([f]) => f).sort()).toEqual([
			'apps/platform/src/lib/illustrations/illustration-outcome.svelte',
			'apps/platform/src/routes/templates/[uid]/+page.svelte',
			'pkgs/ui/src/lib/components/template-authoring/TemplateEditor.svelte',
			'pkgs/ui/src/lib/components/ui/document-viewer/document-viewer.svelte'
		]);
	});

	it('leaves every mount but the thumbnail on the full chrome', () => {
		const optedOut = mounts
			.filter(([, props]) => /searchable=\{false\}|zoomable=\{false\}/.test(props))
			.map(([file]) => file);
		expect(optedOut).toEqual(['apps/platform/src/routes/templates/[uid]/+page.svelte']);
	});

	it('forwards searchable and zoomable so a constrained surface can drop the chrome', () => {
		// DocumentViewer is the only path three reading surfaces have to PdfViewer. Passthrough is
		// deliberate: a dialog (client terms) can opt out of zoom without stripping chrome from the
		// documents record page, which still inherits the defaults.
		const wrapper = readFileSync(
			join(UI, 'ui', 'document-viewer', 'document-viewer.svelte'),
			'utf8'
		);
		expect(wrapper).toMatch(/searchable = true/);
		expect(wrapper).toMatch(/zoomable = true/);
		expect(wrapper).toContain('{searchable}');
		expect(wrapper).toContain('{zoomable}');
	});
});

/**
 * The preview's FAILURE state.
 *
 * Two rules, and a source scan is the only thing that can see either — copy is invisible to
 * svelte-check, and a retry button wired to nothing type-checks perfectly.
 *
 *  1. It names no internal moving part. An adviser cannot act on the state of our renderer, so
 *     asking them about it spends their attention for nothing; the administrator is who the copy
 *     points at.
 *  2. The retry retries. The preview effect re-runs only when something it READS changes, so the
 *     button has to bump a counter the effect references — otherwise it looks like a fix and is a
 *     no-op, which reads as "we tried, it is still broken".
 */
describe('the template preview when a render fails', () => {
	const PAGE = readFileSync(join(ROUTES, '[uid]', '+page.svelte'), 'utf8');

	it('never asks the reader about our infrastructure', () => {
		// Not a copy-freeze. Any rewording is free; naming one of our own moving parts is not.
		for (const leak of ['render worker', 'worker running', 'Is the render']) {
			expect(PAGE.toLowerCase(), leak).not.toContain(leak.toLowerCase());
		}
	});

	it('offers a retry, and the fetch actually re-runs when it is pressed', () => {
		// The counter has to be BOTH incremented by the button and referenced inside the effect.
		expect(PAGE).toContain('previewAttempt++');
		// Referenced in the effect body, not merely declared — a bare `previewAttempt;` statement is
		// what makes the effect depend on it.
		const effect = PAGE.slice(PAGE.indexOf('$effect(() => {'), PAGE.indexOf('const dtm ='));
		expect(effect).toContain('previewAttempt');
	});

	it('says the template itself is unharmed', () => {
		// The reader's real question on seeing a failure over their own work.
		expect(PAGE).toMatch(/template is unchanged/i);
	});
});
