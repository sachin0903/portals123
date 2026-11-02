import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { CLIENT_BLOCKS, clampBlockSpan, minBlockW } from './client-blocks.js';
import {
	PAGE_CAPS,
	PAGE_ICON_NAMES,
	PAGE_ICONS,
	RESERVED_SLUGS,
	SHADOWABLE_SLUGS
} from './portal-pages.js';

/**
 * block-contract.json is the single Python/TS contract for portal customisation:
 * apps/api/app/branding.py validates against it (pinned by apps/api tests) and this registry
 * feeds the studio + renderer. These tests read the JSON via node:fs — independently of the
 * modules under test, which import it — so a drifted literal, a broken import or a stale copy
 * all fail here rather than at a firm's first save.
 */
const contract = JSON.parse(
	readFileSync(new URL('./block-contract.json', import.meta.url), 'utf8')
) as {
	blocks: Record<string, { variants: string[]; looks?: string[]; settings: string[] }>;
	reservedSlugs: string[];
	shadowableSlugs: string[];
	pageIcons: string[];
	caps: Record<string, number>;
	settingLimits: Record<string, number | [number, number]>;
};

const TOGETHER = 'change together with block-contract.json + apps/api/app/branding.py';

describe('CLIENT_BLOCKS pins to block-contract.json', () => {
	it(`covers exactly the contract's block types (${TOGETHER})`, () => {
		expect(new Set(CLIENT_BLOCKS.map((b) => b.type))).toEqual(
			new Set(Object.keys(contract.blocks))
		);
	});

	it(`lists each type once`, () => {
		const types = CLIENT_BLOCKS.map((b) => b.type);
		expect(types.length).toBe(new Set(types).size);
	});

	for (const meta of CLIENT_BLOCKS) {
		describe(meta.type, () => {
			it(`variants equal the contract's list (${TOGETHER})`, () => {
				expect(meta.variants).toEqual(contract.blocks[meta.type]?.variants);
			});

			it(`puts 'auto' first and defaults to it`, () => {
				expect(meta.variants[0]).toBe('auto');
				expect(meta.defaultVariant).toBe('auto');
			});

			it(`declares only settings the contract knows (${TOGETHER})`, () => {
				const allowed = new Set(contract.blocks[meta.type]?.settings);
				const unknown = meta.settings.map((s) => s.key).filter((k) => !allowed.has(k));
				expect(unknown, `unknown settings on ${meta.type}: ${unknown.join(', ')}`).toEqual([]);
			});

			it(`uses snake_case setting keys (they ride the wire as pydantic dumps them)`, () => {
				for (const s of meta.settings) expect(s.key).toMatch(/^[a-z][a-z0-9_]*$/);
			});

			it(`matches the contract's settingLimits (${TOGETHER})`, () => {
				for (const s of meta.settings) {
					const limit = contract.settingLimits[`${meta.type}.${s.key}`];
					if (limit === undefined) continue;
					if (Array.isArray(limit)) expect([s.min, s.max], `${meta.type}.${s.key}`).toEqual(limit);
					else expect(s.maxLength, `${meta.type}.${s.key}`).toBe(limit);
				}
			});

			it(`carries builder copy + sources`, () => {
				expect(meta.label.trim()).not.toBe('');
				expect(meta.description.trim()).not.toBe('');
				expect(Array.isArray(meta.sources)).toBe(true);
				// Only the two data-free blocks may declare no reads.
				if (!['message'].includes(meta.type)) expect(meta.sources.length).toBeGreaterThan(0);
			});
		});
	}
});

describe('per-block looks pin to block-contract.json', () => {
	// The looks axis (per-widget visual designs, orthogonal to variants — looks.ts resolves them):
	// exactly these ten types carry a looks list; portfolioHeader's variants ARE its looks. news
	// joined in Oct 2026 (card · magazine · journal) — the Insights page is where a firm's
	// identity has the most room, so it is the listing whose looks differ most.
	const LOOKS_BEARING = [
		'accounts',
		'allocation',
		'holdings',
		'activity',
		'adviser',
		'documents',
		'actionItems',
		'onboardingProgress',
		'message',
		'news'
	];

	it(`exactly the ten looks-bearing types carry a looks list (${TOGETHER})`, () => {
		const withLooks = Object.entries(contract.blocks)
			.filter(([, b]) => b.looks !== undefined)
			.map(([type]) => type);
		expect(withLooks.sort()).toEqual([...LOOKS_BEARING].sort());
	});

	it(`every looks list leads with 'auto', and all but onboardingProgress offer 'card'`, () => {
		for (const type of LOOKS_BEARING) {
			const looks = contract.blocks[type]?.looks ?? [];
			expect(looks[0], type).toBe('auto');
			// onboardingProgress's carded design IS the banner — it has no separate 'card' look.
			if (type !== 'onboardingProgress') expect(looks, type).toContain('card');
			else expect(looks, type).toContain('banner');
		}
	});

	for (const meta of CLIENT_BLOCKS) {
		const contractLooks = contract.blocks[meta.type]?.looks;
		const descriptor = meta.settings.find((s) => s.key === 'look');
		if (contractLooks) {
			it(`${meta.type}: the look control offers exactly the contract's looks, 'auto' first (${TOGETHER})`, () => {
				expect(descriptor).toBeDefined();
				expect(descriptor?.control).toBe('segmented');
				expect(descriptor?.options?.map((o) => o.value)).toEqual(contractLooks);
				// inspectorValue highlights the FIRST option when the setting is unset, and unset
				// genuinely resolves through the auto path — 'auto' must lead.
				expect(descriptor?.options?.[0]?.value).toBe('auto');
			});

			it(`${meta.type}: every look option carries human copy`, () => {
				for (const o of descriptor?.options ?? []) {
					expect(o.label.trim(), `${meta.type} look '${o.value}'`).not.toBe('');
					// Machine vocabulary must not leak into the builder verbatim-lowercase.
					expect(o.label[0], `${meta.type} look '${o.value}'`).toBe(o.label[0]?.toUpperCase());
				}
			});
		} else {
			it(`${meta.type}: no look control — the contract gives it no looks axis`, () => {
				expect(descriptor).toBeUndefined();
			});
		}
	}
});

describe('the portfolioHeader settings surface', () => {
	const header = CLIENT_BLOCKS.find((b) => b.type === 'portfolioHeader')!;
	const descriptor = (key: string) => header.settings.find((s) => s.key === key);

	it(`declares the contract's variant set — hero/canvas/ledger/compact behind auto (${TOGETHER})`, () => {
		expect(header.variants).toEqual(['auto', 'hero', 'canvas', 'ledger', 'compact']);
	});

	it('actions/default_period lead with the renderer defaults (inspectorValue highlights the FIRST option when the setting is unset, so first-slot must be what unset actually renders)', () => {
		expect(descriptor('actions')?.options?.[0]?.value).toBe('deposit_withdraw');
		expect(descriptor('default_period')?.options?.[0]?.value).toBe('6M');
	});

	it('actions offers exactly the contract-frozen modes', () => {
		expect(descriptor('actions')?.options?.map((o) => o.value)).toEqual([
			'deposit_withdraw',
			'contact',
			'none'
		]);
	});

	it('default_period offers exactly the period keys', () => {
		expect(new Set(descriptor('default_period')?.options?.map((o) => o.value))).toEqual(
			new Set(['1M', '3M', '6M', '1Y'])
		);
	});
});

describe('the listing toolbar switches', () => {
	// The legacy listing pages' search/filter affordances, absorbed by the widgets: holdings,
	// activity and documents each carry a `toolbar` switch, and documents additionally the
	// outstanding-requests banner. All default OFF (`defaultOn: false`) — the widgets read
	// `=== true`, and the inspector must agree (pages-view inspectorValue) or a fresh block's
	// switch would lie about the render.
	const descriptor = (type: string, key: string) =>
		CLIENT_BLOCKS.find((b) => b.type === type)?.settings.find((s) => s.key === key);

	for (const type of ['holdings', 'activity', 'documents']) {
		it(`${type}: declares a default-off 'toolbar' switch (${TOGETHER})`, () => {
			const d = descriptor(type, 'toolbar');
			expect(d?.control).toBe('switch');
			expect(d?.defaultOn).toBe(false);
			expect(d?.label.trim()).not.toBe('');
		});
	}

	it(`documents: declares a default-off 'show_requests' switch (${TOGETHER})`, () => {
		const d = descriptor('documents', 'show_requests');
		expect(d?.control).toBe('switch');
		expect(d?.defaultOn).toBe(false);
	});

	it('no other block type surfaces a toolbar control', () => {
		for (const meta of CLIENT_BLOCKS) {
			if (['holdings', 'activity', 'documents'].includes(meta.type)) continue;
			expect(
				meta.settings.some((s) => s.key === 'toolbar'),
				meta.type
			).toBe(false);
		}
	});

	it('every historic switch keeps the absent-means-on contract (no defaultOn declared)', () => {
		for (const meta of CLIENT_BLOCKS)
			for (const s of meta.settings) {
				if (s.control !== 'switch' || ['toolbar', 'show_requests'].includes(s.key)) continue;
				expect(s.defaultOn, `${meta.type}.${s.key}`).toBeUndefined();
			}
	});
});

describe('block span floors (minW) + the builder resize gate', () => {
	// minW is deliberately UI-plane-only (not in the contract) — see BlockMeta.minW. These tests
	// pin the floors and the clamp so a builder refactor can't quietly re-allow the crushed-table
	// layouts the floors exist to prevent.
	for (const meta of CLIENT_BLOCKS) {
		it(`${meta.type}: 1 ≤ minW ≤ defaultW (a default below its own floor is a contradiction)`, () => {
			expect(meta.minW).toBeGreaterThanOrEqual(1);
			expect(meta.minW).toBeLessThanOrEqual(meta.defaultW);
		});
	}

	it('every table-bearing block floors at half the grid', () => {
		for (const type of ['accounts', 'holdings', 'activity', 'documents'])
			expect(minBlockW(type), type).toBe(6);
	});

	it('clampBlockSpan refuses a resize below the type floor and above the grid bounds', () => {
		expect(clampBlockSpan('holdings', 2, 1)).toEqual({ w: 6, h: 1 });
		expect(clampBlockSpan('adviser', 1, 1)).toEqual({ w: 3, h: 1 });
		expect(clampBlockSpan('message', 1, 0)).toEqual({ w: 2, h: 1 });
		expect(clampBlockSpan('holdings', 99, 99)).toEqual({ w: 12, h: 6 });
	});

	it('leaves an in-range resize untouched', () => {
		expect(clampBlockSpan('holdings', 8, 2)).toEqual({ w: 8, h: 2 });
	});

	it('unknown types clamp to the generic 1–12 (no floor we can vouch for)', () => {
		expect(minBlockW('mystery')).toBe(1);
		expect(clampBlockSpan('mystery', 0, 0)).toEqual({ w: 1, h: 1 });
	});
});

describe('page constants pin to block-contract.json', () => {
	it(`RESERVED_SLUGS equals the contract's list (${TOGETHER})`, () => {
		expect([...RESERVED_SLUGS].sort()).toEqual([...contract.reservedSlugs].sort());
	});

	it(`SHADOWABLE_SLUGS equals the contract's list (${TOGETHER})`, () => {
		expect([...SHADOWABLE_SLUGS].sort()).toEqual([...contract.shadowableSlugs].sort());
	});

	it(`PAGE_ICON_NAMES equals the contract's list (${TOGETHER})`, () => {
		expect([...PAGE_ICON_NAMES]).toEqual(contract.pageIcons);
	});

	it(`caps equal the contract's (${TOGETHER})`, () => {
		expect({ ...PAGE_CAPS }).toEqual(contract.caps);
	});

	it(`ships an icon component for every curated name, and no strays`, () => {
		expect(Object.keys(PAGE_ICONS).sort()).toEqual([...contract.pageIcons].sort());
		for (const [name, component] of Object.entries(PAGE_ICONS))
			expect(component, `PAGE_ICONS['${name}']`).toBeTruthy();
	});

	it(`keeps reserved and shadowable slugs disjoint — a shadowable slug must be takeable`, () => {
		for (const slug of SHADOWABLE_SLUGS) expect(RESERVED_SLUGS.has(slug), slug).toBe(false);
	});
});
