/**
 * AN INLINE IMAGE WORKS IN THE PORTALS AND NOWHERE IN EMAIL, and until this it said so nowhere.
 *
 * Branding accepts a `data:` URI — the portals render one happily, and an upload falls back to inlining
 * when no object-store bucket is configured. Every mail client blocks them. So a firm that had uploaded a
 * logo still got its NAME set as type in every email, with nothing anywhere to explain it.
 *
 * The resolution was already correct (`app/email/transport.py::masthead_image` refuses an inline value and falls
 * through to the icon, then to type). The SILENCE was the bug.
 *
 * Asserted as source shapes, the same way `document-head.test.ts` and `rail.test.ts` do — this component
 * needs a live editor and there is no jsdom in this tree. What can be pinned is which assets carry the
 * warning and what triggers it, and that is exactly what a later edit would get wrong.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(join(import.meta.dirname, 'branding-editor.svelte'), 'utf8');

describe('the inline-image warning', () => {
	it('fires on a data: URI and nothing else', () => {
		// `startsWith('data:')` on the trimmed value — not a regex over the whole string, which would also
		// match an https URL that happens to contain the word.
		expect(source).toMatch(/const isInlineImage[\s\S]{0,300}?startsWith\('data:'\)/);
	});

	it('is shown only on the assets email actually reads', () => {
		// `logo_url` and `icon_url` — the two `masthead_image` resolves between. `logo_dark_url` and
		// `favicon_url` are deliberately excluded: email has no reliable colour-scheme image swap and no
		// browser tab, so a warning on those would be noise about something that was never going to work.
		const fields = /const ASSET_FIELDS:[\s\S]*?\n\t\];/.exec(source)?.[0] ?? '';
		expect(fields, 'ASSET_FIELDS not found').not.toBe('');
		const withEmailUse = [...fields.matchAll(/prop: '(\w+)',[\s\S]{0,120}?emailUse: true/g)].map(
			(m) => m[1]
		);
		expect(withEmailUse.sort()).toEqual(['icon_url', 'logo_url']);
	});

	it('is gated on BOTH the asset mattering and the value being inline', () => {
		// Either half alone is wrong: without `emailUse` it nags about the favicon, and without the value
		// test it nags at every firm whose uploads reach the object store.
		expect(source).toContain('{#if f.emailUse && isInlineImage(assetUrl(f.prop))}');
	});

	it('says what happens instead, not just that something is wrong', () => {
		expect(source).toContain('may not appear in email');
		expect(source).toContain('Use a publicly hosted image');
	});

	it('uses the warning tone as TEXT, not the on-fill ink', () => {
		// `--warning-foreground` is the ink for text ON a warning fill — near-white in light mode, so it
		// would have been invisible on the card. `-strong` is the tone as text on a normal surface, which
		// is the idiom every other warning line in pkgs/ui uses.
		expect(source).toContain('text-warning-strong');
		expect(source).not.toContain('text-warning-foreground');
	});
});

describe('client portal design sets', () => {
	it('assigns a design from the client book, never from an auth-profile role', () => {
		// The role picker is GONE, not merely unused. Advised vs D2C is a book fact and both books
		// share one tenant (docs/client-classifications.md), so a WorkOS role could never split
		// them — a studio control that implied otherwise was the whole defect. Assignment now hangs
		// off the book row in the catalogue, which is also the only place the two facts a firm is
		// really choosing between — which portal, and whether money moves — sit together.
		expect(source).toContain('Client portal design sets');
		expect(source).toContain('<ClassificationCatalogue');
		expect(source).toContain('onAssignDesign={assignBookDesign}');
		expect(source).not.toContain('setTemplateRoles');
		expect(source).not.toContain('Client roles');
	});

	it('previews the selected pages in Design view instead of the legacy flat layout', () => {
		expect(source).toContain('materialiseWorkingPages');
		expect(source).not.toContain('defaultClientLayout');
	});

	it('unproxies every value it hands to a deep-cloning helper', () => {
		// materialiseWorkingPages and templateFromBranding both deep-copy with structuredClone,
		// which throws DataCloneError on a $state proxy. The first version of this test asserted
		// the Design preview's call verbatim -- `materialiseWorkingPages(active.pages)` -- and so
		// pinned the defect rather than the rule: that call reads a proxy straight off `$derived`
		// state, and it is the DEFAULT view, so the studio failed on hydration in every browser
		// while every server-side test passed. The rule is what is worth asserting, and
		// branding-editor.dom.test.ts is what observes it.
		const calls = [...source.matchAll(/materialiseWorkingPages\(([\s\S]{0,120})/g)];
		expect(calls.length, 'no materialiseWorkingPages call found').toBeGreaterThan(0);
		for (const [, argument] of calls) {
			expect(argument, `clone input is not snapshotted: ${argument}`).toContain('$state.snapshot');
		}
	});

	it('Save stamps the working page set onto the PUT, not only the draft snapshot', () => {
		// Drafts.pages stays null until an edit; the working copy is local. Snapshotting drafts
		// alone toasted Saved while the PUT echoed the previous composition.
		expect(source).toContain('withWorkingPages');
		expect(source).toMatch(/function save\([\s\S]*?withWorkingPages/);
		expect(source).toContain('pagesDirty');
	});
});

describe('the adviser portal is identity and two accents', () => {
	it('does not generate a full palette for the staff tool', () => {
		expect(source).toContain("if (portal === 'client') regenerate()");
		expect(source).toContain('applyAccents(active, seeds.advisor.primary, seeds.advisor.accent)');
		expect(source).toContain('staffPortalBranding');
		expect(source).toContain('Paper, type and chrome stay');
		expect(source).toContain("label: 'Adviser'");
		expect(source).toContain("'Adviser portal'");
		expect(source).not.toContain('Advisor portal');
		expect(source).not.toContain("label: 'Advisor'");
	});
});

describe('colour is seed-only', () => {
	it('offers no per-token palette override', () => {
		expect(source).not.toContain('Advanced colour settings');
		expect(source).not.toContain('PALETTE_GROUPS');
	});

	it('offers card designs on the client portal only, one row per product the firm offers', () => {
		const clientBranch = source.slice(source.indexOf('<!-- Card designs -->'));
		expect(clientBranch.indexOf('Card designs')).toBeLessThan(clientBranch.indexOf('Accents'));
		expect(source).toContain('{#each cardRows as product (product.type)}');
		expect(source).toContain('const cardRows = $derived(productTypes)');
		expect(source).not.toContain('CARD_PRODUCT_TYPES');
		expect(source).toContain("onupload(portal, 'card-art', file)");
	});
});
