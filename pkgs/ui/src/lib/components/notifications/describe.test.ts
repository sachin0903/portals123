import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
	audienceKind,
	categoryMeta,
	configuredMeta,
	configuredState,
	relativeTime,
	resourceCategory,
	stateLabel,
	toneClasses,
	triggersFrom
} from './describe.js';
import { WRAPPER_CODE } from './rule-types.js';

describe('categoryMeta', () => {
	it('maps known categories to a label + tone', () => {
		expect(categoryMeta('tasks').label).toBe('Task');
		expect(categoryMeta('documents').label).toBe('Document');
		expect(categoryMeta('messages').label).toBe('Message');
		expect(categoryMeta('messages').tone).toBe('info');
		expect(categoryMeta('accounts').tone).toBe('info');
	});

	it('falls back neutrally for an unknown category', () => {
		const meta = categoryMeta('something-new');
		expect(meta.label).toBe('Update');
		expect(meta.tone).toBe('default');
	});
});

describe('toneClasses', () => {
	it('never emits a raw palette colour for the branded tones', () => {
		// info reads the branded primary; default reads muted — neither hard-codes a hex/palette.
		expect(toneClasses('info')).toContain('primary');
		expect(toneClasses('default')).toContain('muted');
	});
});

describe('relativeTime', () => {
	const now = new Date('2026-08-06T12:00:00Z');

	it('reads recent stamps as coarse relative phrases', () => {
		expect(relativeTime('2026-08-06T11:59:40Z', now)).toBe('just now');
		expect(relativeTime('2026-08-06T11:55:00Z', now)).toBe('5m ago');
		expect(relativeTime('2026-08-06T10:00:00Z', now)).toBe('2h ago');
		expect(relativeTime('2026-08-04T12:00:00Z', now)).toBe('2d ago');
	});

	it('falls back to a short date beyond a week', () => {
		expect(relativeTime('2026-07-01T12:00:00Z', now)).toBe('1 Jul');
	});

	it('returns empty for an unparseable stamp rather than throwing', () => {
		expect(relativeTime('not-a-date', now)).toBe('');
	});

	it('reads a stamp ahead of the reader as "just now", never a negative age', () => {
		// Server and browser clocks disagree by seconds routinely and by minutes on a machine back
		// from sleep, so a notification written moments ago can carry a stamp in the reader's future.
		// The 50s case is the one that used to render "-1m ago": it clears the 45s "just now" branch
		// on magnitude, and `Math.floor(-50 / 60)` is -1 rather than 0.
		expect(relativeTime('2026-08-06T12:00:50Z', now)).toBe('just now');
		expect(relativeTime('2026-08-06T12:05:00Z', now)).toBe('just now');
		expect(relativeTime('2026-08-06T13:00:00Z', now)).toBe('just now');
	});
});

/** THE CROSS-LANGUAGE PIN for the category table.
 *
 *  Both directions, but the one that MATTERS is a live category missing from here: `messages` was
 *  absent while live on both portals, so every chat notice and every messaging row in the rules list
 *  wore the neutral "Update" bell — present but unlabelled, which reads as not offered at all. A
 *  `Record` lookup cannot fail for a key nobody added, so only this can catch it.
 *
 *  The other direction is the aspirational entry. Python's own rule is that "a category earns its
 *  place by having a rule that fires off a real event on the bus"; a label here for a category that
 *  never notifies is a branch no notification takes. */
describe('parity with app/notifications/registry.py::CATEGORIES', () => {
	const source = readFileSync(
		join(
			fileURLToPath(new URL('../../../../../../', import.meta.url)),
			'apps/api/app/notifications/registry.py'
		),
		'utf8'
	);

	const pythonCategories = (): string[] => {
		const body = source.split('CATEGORIES: dict[str, Category] = {')[1]?.split('\n}')[0] ?? '';
		return [...body.matchAll(/^\s{4}"([a-z_]+)": Category\(/gm)].map((m) => m[1]).sort();
	};

	it('parses the Python, so the assertion below cannot pass vacuously', () => {
		expect(pythonCategories()).toContain('messages');
		expect(pythonCategories().length).toBeGreaterThan(3);
	});

	it('gives every live category its own label, never the neutral fallback', () => {
		// Behavioural, through the exported accessor: this is the direction that bit, and a
		// `Record` lookup silently answers the fallback for a key nobody added.
		for (const category of pythonCategories())
			expect(categoryMeta(category).label, category).not.toBe('Update');
	});

	it('spells the wrapper sentinel as the catalogue does', () => {
		// One string doing three jobs — a code, a category name, and the fallback a rule with no
		// chosen template takes. The fallback is the one that is a SENTINEL, so it is named on both
		// sides; a divergence sends every un-chosen rule through a template code that does not exist.
		const catalogue = readFileSync(
			join(
				fileURLToPath(new URL('../../../../../../', import.meta.url)),
				'apps/api/app/email/catalog.py'
			),
			'utf8'
		);
		expect(catalogue.match(/^WRAPPER_CODE = "(.+?)"$/m)?.[1]).toBe(WRAPPER_CODE);
	});

	it('labels nothing that never notifies', () => {
		// The table is module-private, so its keys are read from the source rather than exported
		// purely for a test. Python's rule is that a category earns its place by having a rule that
		// fires a real event, so a label here for one that cannot is a branch nothing takes.
		const ours = readFileSync(join(import.meta.dirname, 'describe.ts'), 'utf8');
		const table = ours.split('const CATEGORY_META: Record<string, CategoryMeta> = {')[1] ?? '';
		const keys = [...table.split('};')[0].matchAll(/^\s+([a-z_]+):/gm)].map((m) => m[1]).sort();
		expect(keys, 'the table moved — this pin reads it by shape').not.toEqual([]);
		expect(keys).toEqual(pythonCategories());
	});
});

describe('the event-registry vocabulary', () => {
	it('folds a resource into a category a firm thinks in', () => {
		expect(resourceCategory('document_request')).toBe('Documents');
		expect(resourceCategory('contribution')).toBe('Money');
		expect(resourceCategory('aml')).toBe('Compliance');
		expect(resourceCategory('authentication')).toBe('Security');
	});

	it('calls an unmapped resource System rather than guessing a category for it', () => {
		// Branding, a role, a sync — those ARE platform plumbing, and saying so is more use than
		// inventing a category per resource.
		expect(resourceCategory('branding')).toBe('System');
		expect(resourceCategory('')).toBe('System');
	});

	it('names each registry state in the reader’s words', () => {
		expect(stateLabel('assignable')).toBe('Can notify someone');
		expect(stateLabel('declarable')).toBe('Needs a subject source');
		expect(stateLabel('actor_only')).toBe('Actor or role only');
	});

	it('falls back to the raw state rather than inventing a label', () => {
		expect(stateLabel('something_new')).toBe('something_new');
	});
});

describe('triggersFrom', () => {
	const shared = { key: 'message.received_by_client' };

	it('de-duplicates a trigger that covers several events', () => {
		// The crash this exists to stop: the editor's Event select is a keyed {#each} over this, and
		// two entries with the same key throw `each_key_duplicate` mid-render — the dialog opens its
		// overlay and draws no content, which reads as a dead button rather than an error.
		const out = triggersFrom([
			{ trigger: shared },
			{ trigger: shared },
			{ trigger: { key: 'document.shared' } }
		]);
		expect(out.map((t) => t.key)).toEqual(['message.received_by_client', 'document.shared']);
	});

	it('skips events with no trigger', () => {
		expect(triggersFrom([{ trigger: null }, { trigger: shared }])).toEqual([shared]);
	});

	it('keeps the first sighting, so order follows the events', () => {
		const a = { key: 'a' };
		const b = { key: 'b' };
		expect(triggersFrom([{ trigger: b }, { trigger: a }, { trigger: b }])).toEqual([b, a]);
	});

	it('is empty for no events', () => {
		expect(triggersFrom([])).toEqual([]);
	});
});

describe('configuredState', () => {
	it('is none when nothing is attached', () => {
		expect(configuredState([])).toBe('none');
	});

	it('is live when any attached notification is on', () => {
		expect(configuredState([{ enabled: false }, { enabled: true }])).toBe('live');
	});

	it('is off — not live — when everything attached is switched off', () => {
		// The distinction the dot exists for: two notifications both switched off tell nobody
		// anything, and reading that as configured is how a firm concludes a notice is going out
		// when it is not.
		expect(configuredState([{ enabled: false }, { enabled: false }])).toBe('off');
	});

	it('gives the unconfigured state no colour of its own', () => {
		// An event nobody has attached anything to is not a fault, so it is hollow rather than red.
		expect(configuredMeta('none').classes).toContain('bg-transparent');
		expect(configuredMeta('live').classes).toContain('--success');
		expect(configuredMeta('off').classes).toContain('--warning');
	});
});

describe('audienceKind', () => {
	it('calls the client the only audience outside the firm', () => {
		expect(audienceKind('client')).toBe('client');
	});

	it('treats every internal audience alike', () => {
		// adviser/assignee/actor are all "somebody at the firm" — three separate marks would say
		// nothing a reader needs on a line.
		for (const key of ['adviser', 'assignee', 'actor']) {
			expect(audienceKind(key)).toBe('staff');
		}
	});

	it('counts an unknown audience as staff, not as client-facing', () => {
		// The safe error: a new audience is far likelier to be an internal role, and marking an
		// internal notice as reaching a customer is the worse way to be wrong.
		expect(audienceKind('compliance_officer')).toBe('staff');
	});
});

describe('the dot legend covers every state the dot can take', () => {
	it('names all three, because an unexplained colour reads as unlabelled rather than as absent', () => {
		// The legend lives in the component (it is markup), so this reads it off the source — the
		// same scan `rules-listing.test.ts` uses. The 'off' entry was dropped once while
		// `configuredState` still produced it, leaving an amber dot nothing on screen explained.
		const source = readFileSync(
			new URL('./notification-rules.svelte', import.meta.url),
			'utf8'
		).replace(/<!--[\s\S]*?-->/g, '');
		const legend = source.slice(source.indexOf('const DOT_LEGEND'));
		const block = legend.slice(0, legend.indexOf('];'));
		for (const state of ['live', 'off', 'none']) {
			expect(block, `the dot can be '${state}' — the legend must say so`).toContain(
				`state: '${state}'`
			);
		}
	});
});
