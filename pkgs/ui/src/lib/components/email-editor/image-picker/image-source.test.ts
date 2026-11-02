import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Choosing the image an email block shows. Asserted over the source for the same reason every other
 * test in this folder is: the editor needs GrapesJS and a browser, so a render can only ever show the
 * skeleton.
 *
 * IT IS A SIDEBAR PANEL, NOT A MODAL. It was a dialog, and that was the wrong shape: choosing a
 * picture is one property of the selected element, and the rail already holds its source, its
 * alternative text and its fallback — so the dialog covered the canvas to answer a question about it,
 * and carried a link field of its own beside the one in the rail, two boxes for one property.
 *
 * What matters here is that it cannot produce a source an inbox will not draw, that it is ours rather
 * than GrapesJS's modal repainted, and that it reaches the firm's images only through the endpoint the
 * app hands it.
 */
const HERE = import.meta.dirname;

/** Comments stripped: this file asserts the ABSENCE of strings the module header legitimately
 *  discusses (`data:`, `gjs-am-`), the false positive `rail.test.ts` records. */
const read = (path: string) =>
	readFileSync(join(HERE, path), 'utf8')
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(?<!:)\/\/[^\n]*/g, '');

const panel = read('image-source.svelte');
const editor = read('../email-editor.svelte');
const barrel = readFileSync(join(HERE, '../index.ts'), 'utf8');

describe('the panel is ours, and app-agnostic', () => {
	it('is built from @graphene/ui primitives via relative paths, not GrapesJS chrome', () => {
		// The same rule the block palette and the style rail follow: a third-party modal painted with
		// `!important` can never match the product, because the markup is not ours. And a self-import of
		// `@graphene/ui` inside pkgs/ui is a cycle, so every part comes in as `../../ui/<part>`.
		expect(panel).not.toMatch(/from '@graphene\/ui/);
		expect(panel).not.toMatch(/from '\$app\//);
		expect(panel).not.toMatch(/from '\$lib\//);
		expect(panel).not.toMatch(/gjs-am-/);
		// No native control anywhere: the file input lives inside `FileDropzone`, which owns the hidden
		// element, the drag handling and the accept/size checks.
		expect(panel).not.toMatch(/<input\b/);
		expect(panel).not.toMatch(/<select\b/);
		expect(panel).toMatch(/<FileDropzone/);
	});

	it('is NOT a dialog any more', () => {
		// The whole point of the rework. A Dialog import here would put it back behind the canvas.
		expect(panel).not.toMatch(/from '\.\.\/\.\.\/ui\/dialog\/index\.js'/);
		expect(panel).not.toMatch(/Dialog\./);
		expect(panel).not.toMatch(/\$bindable/);
	});

	it('is the ONE place a URL is typed — not a second link box beside the rail', () => {
		// The Source field lives HERE now (URL · upload on one line). A second paste-a-link
		// box was how the dialog disagreed with the rail about what the source was.
		expect(panel).not.toMatch(/paste a link/i);
		expect(panel).toMatch(/id="et-img-src"/);
		expect(panel).toMatch(/<Input/);
	});

	it('POSTs to the endpoint it was handed, and offers no upload without one', () => {
		expect(panel).toMatch(/await fetch\(libraryUrl, \{ method: 'POST', body \}\)/);
		expect(panel).toMatch(/if \(!libraryUrl \|\| uploading\) return/);
		// No proxy ⇒ no upload chip, leaving the URL field as the whole affordance.
		expect(panel).toMatch(/\{#if libraryUrl\}/);
	});

	it('BROWSES NOTHING — deliberately, until per-firm image storage exists', () => {
		// It listed the tenant bucket's public prefix and drew the stored images as thumbnails, which
		// cost most of a 320px rail to answer a question ("which of my images?") that cannot be asked
		// properly yet. One upload until then; browsing returns with that work.
		expect(panel).not.toMatch(/LoadErrorBanner/);
		expect(panel).not.toMatch(/Skeleton/);
		expect(panel).not.toMatch(/grid-cols/);
		expect(panel).not.toMatch(/method: 'GET'|headers: \{ Accept/);
		// Word-bounded: `loadError` is a substring of `uploadError`, which legitimately stays.
		expect(panel).not.toMatch(/\bnoLibrary\b|\bloadError\b|library\?\.images/);
	});
});

describe('uploading', () => {
	it('states what an UNHOSTED upload costs, and still applies it', () => {
		// Refusing left an author with an upload control that never accepted anything. The standing
		// warning went with the listing — `hosted` is only known once something has been uploaded.
		expect(panel).toMatch(/added\.hosted === false/);
		expect(panel).toMatch(/publicly hosted image/);
		expect(panel).toMatch(/if \(added\.url\) onpick\?\.\(added\.url\)/);
	});

	it('starts on the file rather than behind a second button', () => {
		expect(panel).toMatch(/if \(file\) void upload\(file\)/);
	});

	it("accepts only the two formats the API can vouch for, at the API's own cap", () => {
		expect(panel).toMatch(/accept="image\/png,image\/jpeg"/);
		// 512 KiB — `storage.MAX_ASSET_BYTES`. Stated client-side so a too-large file is refused before
		// it is sent, not as a 413 after.
		expect(panel).toMatch(/maxBytes=\{512 \* 1024\}/);
	});

	it('is an ICON chip on the source line, not the rich or slim dropzone', () => {
		expect(panel).not.toMatch(/\brich\b/);
		expect(panel).toMatch(/size="icon"/);
	});
});

describe('the editor hosts it from the curated Image band', () => {
	it('offers Source + alt for content images, and a wordmark-only rail for the masthead', () => {
		const band =
			editor.match(
				/\{#if imageTarget && isBrandLogo\(imageTarget\)\}([\s\S]*?)\{:else if imageTarget\}([\s\S]*?)\{\/if\}/
			) ?? null;
		expect(band, 'the Image band branches were not found').not.toBeNull();
		const masthead = band?.[1] ?? '';
		const content = band?.[2] ?? '';

		// Content images: URL · upload + alt. No "If no image" — that is a masthead concern.
		expect(content).toMatch(/label="Source"/);
		expect(content).toMatch(/set=\{isUsableInEmail\(imageSrc\)\}/);
		expect(content).toMatch(/<ImageSource/);
		expect(content).toMatch(/src=\{imageSrc\}/);
		expect(content).toMatch(/label="Alternative text"/);
		expect(content).not.toMatch(/If no image|Wordmark|et-img-fallback/);
		expect(panel).toMatch(/id="et-img-src"/);

		// Masthead: no source/upload/alt — wordmark text only, while brand image storage is unfinished.
		expect(masthead).toMatch(/wordmark for now/i);
		expect(masthead).toMatch(/label="Wordmark"/);
		expect(masthead).toMatch(/id="et-img-fallback"/);
		expect(masthead).not.toMatch(/<ImageSource|label="Source"|label="Alternative text"/);

		expect(content).not.toMatch(/\bhint=/);
		expect(masthead).not.toMatch(/\bhint=/);
		expect(content).not.toMatch(/<button/);
		expect(masthead).not.toMatch(/<button/);
	});

	it('writes the source onto the GrapesJS MODEL, not the attribute', () => {
		// An image's `src` is a model property: the view redraws on `change:src` and `getAttrToHTML`
		// overwrites any `src` attribute with it, so writing the attribute changes the export and leaves
		// the canvas showing the old picture.
		const apply = editor.match(/function applyImageSrc\([\s\S]*?\n\t\}/)?.[0] ?? '';
		expect(apply, 'applyImageSrc not found').not.toBe('');
		expect(apply).toMatch(/imageTarget\.set\?\.\('src', imageSrc\)/);
		expect(apply).not.toMatch(/addAttributes/);
		// A real content change, so it emits rather than waiting for a GrapesJS change event.
		expect(apply).toMatch(/emitNow\(\)/);
		expect(apply).toMatch(/if \(!imageTarget \|\| readOnly\) return;/);
	});

	it('mounts no image dialog at all', () => {
		expect(editor).not.toMatch(/<ImagePicker/);
		expect(editor).not.toMatch(/imagePickerOpen/);
		expect(editor.match(/<ImageSource/g)?.length).toBe(1);
	});

	it("shuts GrapesJS's own asset shell, which `custom: true` still opens", () => {
		// `assetManager: { custom: true }` stops it RENDERING an asset manager, not opening the modal
		// that would have held one — so a double-click on an image put an empty, unstyled
		// "Select Image" dialog on screen. Nothing of ours opens on it now: selecting the image already
		// shows the rail's Image band.
		const handler = editor.match(/ed\.on\('asset:custom'[\s\S]*?\n\t\t\t\}\);/)?.[0] ?? '';
		expect(handler, 'the asset:custom handler was not found').not.toBe('');
		expect(handler).toMatch(/ed\.Modal\?\.close\?\.\(\)/);
	});

	it("defaults the library URL to a path relative to the editor's own route", () => {
		// Both mounts put the proxy beside the page they mount from, so one relative default reaches the
		// right one in either portal without the host page threading a URL down.
		expect(editor).toMatch(/imagesUrl = 'images'/);
		expect(editor).toMatch(/libraryUrl=\{imagesUrl\}/);
	});

	it("reads the selection's source when the selection changes", () => {
		const sync = editor.match(/function syncFieldsFromSelection\(\)[\s\S]*?\n\t\}/)?.[0] ?? '';
		expect(sync).toMatch(/const rawSrc = String\(imageTarget\?\.get\?\.\('src'\)/);
		// GrapesJS's placeholder is a raw `<svg …>` string in the model's own `src`; read as none.
		expect(sync).toMatch(/isPlaceholderSource\(rawSrc\) \? '' : rawSrc/);
	});

	it('is exported from the barrel with the rules the send side also reads', () => {
		expect(barrel).toMatch(
			/export \{ default as ImageSource \} from '\.\/image-picker\/image-source\.svelte';/
		);
		expect(barrel).toMatch(/isUsableInEmail/);
	});
});
