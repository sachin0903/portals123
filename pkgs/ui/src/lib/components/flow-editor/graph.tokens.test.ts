/**
 * The flow-graph node fills must stay TOKEN-DERIVED.
 *
 * Why this test exists. A graph needs visually distinguishable actors, which is a standing invitation
 * to hardcode three nice-looking hues — and because these values are consumed as inline
 * `style="background:…"` they are invisible to every type gate and to a Tailwind-class colour sweep,
 * so a regression here ships silently. This pins the decision recorded in graph.ts: each actor and
 * category resolves from a theme token via relative-colour syntax, so re-branding re-hues the graph
 * and nothing is a literal.
 *
 * It also pins the two properties that make the fills SAFE rather than merely branded: every actor
 * uses a different token (three actors that collapse onto one hue are useless), and the lightness is
 * pinned rather than inherited (a raw `var(--info)` is light enough in dark mode to lose the
 * near-white ink the nodes draw on top).
 */
import { describe, it, expect } from 'vitest';
import {
	ACTOR_STYLE,
	ACTOR_LABEL,
	CATEGORY_STYLE,
	CONTROL_ACCENT,
	actorOf,
	categoryOf
} from './graph';
import type { Actor, Category } from './graph';
import type { FlowNode, FlowStep } from '@graphene/ui/book/journeys';

/** The three actors that are PEOPLE. `system` is deliberately excluded from the hue assertions: an
 *  action node has no owner, so it wears the hue-less ink surface rather than a fourth colour, and
 *  asserting a pinned lightness on it would be asserting the opposite of the decision. */
const ACTORS: Actor[] = ['client', 'adviser', 'compliance'];
const CATEGORIES: Category[] = ['client', 'gate', 'action'];

/** Every colour string the graph hands to an inline style. */
const allFills = [
	...[...Object.values(ACTOR_STYLE), ...Object.values(CATEGORY_STYLE)].flatMap((s) => [
		s.bg,
		s.head,
		s.sub
	]),
	...Object.values(CONTROL_ACCENT)
];

describe('flow-graph node fills are token-derived, not literal', () => {
	it('contains no hex, rgb() or hsl() literal anywhere', () => {
		for (const fill of allFills) {
			expect(fill, `"${fill}" is a literal colour — derive it from a theme token`).not.toMatch(
				/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/
			);
		}
	});

	it('every fill resolves from a CSS custom property', () => {
		for (const fill of allFills) {
			expect(fill, `"${fill}" does not reference a token`).toMatch(/var\(--[a-z-]+\)/);
		}
	});

	it('gives each actor its own token, so the three stay tellable apart', () => {
		const tokensFor = (a: Actor) => (ACTOR_STYLE[a].bg.match(/var\((--[a-z-]+)\)/) ?? [])[1];
		const used = ACTORS.map(tokensFor);
		expect(used.every(Boolean), 'an actor fill has no token').toBe(true);
		expect(new Set(used).size, `actors share a token: ${used.join(', ')}`).toBe(ACTORS.length);
	});

	it('pins node lightness so the on-node ink cannot be lost in dark mode', () => {
		// `oklch(from var(--x) <L> c h)` — an explicit L, hue and chroma inherited. A bare
		// `var(--info)` would track the token's own lightness, which lifts under .dark.
		for (const a of ACTORS) {
			expect(ACTOR_STYLE[a].bg).toMatch(/oklch\(from var\(--[a-z-]+\) 0?\.\d+ c h\)/);
			// The header band must be darker than the body, or the node reads inside-out.
			const l = (v: string) => Number(/oklch\(from var\(--[a-z-]+\) (0?\.\d+)/.exec(v)![1]);
			expect(l(ACTOR_STYLE[a].head)).toBeLessThan(l(ACTOR_STYLE[a].bg));
		}
	});

	it('labels every actor, so colour is never the only channel', () => {
		// The accessibility half of the decision in graph.ts: --info and --brand are close hues, which
		// is tolerable only while each node also carries the actor's name.
		for (const a of ACTORS) expect(ACTOR_LABEL[a]?.trim()).toBeTruthy();
		expect(new Set(ACTORS.map((a) => ACTOR_LABEL[a])).size).toBe(ACTORS.length);
	});

	it('covers every actor and category — no fill falls through to undefined', () => {
		for (const a of ACTORS) expect(ACTOR_STYLE[a]?.bg).toBeTruthy();
		for (const c of CATEGORIES) expect(CATEGORY_STYLE[c]?.bg).toBeTruthy();
	});

	it('gives the system actor the hue-less ink surface, not a fourth colour', () => {
		// An action node is nobody's work. Reaching for another hue would say it belongs to somebody,
		// and would also be the fourth of four hues that all have to stay apart under any brand.
		expect(ACTOR_STYLE.system).toEqual(CATEGORY_STYLE.action);
		expect(ACTOR_LABEL.system?.trim()).toBeTruthy();
	});

	it('accents every control kind from a token', () => {
		// Control nodes are outlined rather than filled (they are not somebody's to-do), so their
		// colour arrives as a border/icon accent — same rule, different property.
		for (const kind of ['branch', 'wait', 'wait_for_event', 'start_flow', 'exit']) {
			expect(CONTROL_ACCENT[kind], `no accent for ${kind}`).toMatch(/^var\(--[a-z-]+\)$/);
		}
	});
});

describe('actor/category derivation', () => {
	const step = (over: Partial<FlowStep>) =>
		({ id: 's', type: 'fill_form', title: 'T', ...over }) as FlowStep;

	it('a client-assigned step is the client actor whatever its type', () => {
		expect(actorOf(step({ assignee: 'client', type: 'aml_check' }))).toBe('client');
	});

	it('compliance owns the AML and review gates', () => {
		expect(actorOf(step({ type: 'aml_check' }))).toBe('compliance');
		expect(actorOf(step({ type: 'review' }))).toBe('compliance');
	});

	it('everything else falls to the adviser', () => {
		expect(actorOf(step({ type: 'setup_account' }))).toBe('adviser');
	});

	it('an action node is the system, whatever its type says', () => {
		// `generate_document` names both a staff step and an unattended action; the KIND decides.
		const action = { id: 'a', kind: 'action', type: 'generate_document' } as unknown as FlowNode;
		expect(actorOf(action)).toBe('system');
	});

	it('an unknown step type still lands on a real category', () => {
		expect(CATEGORIES).toContain(categoryOf('not_a_real_type' as never));
	});
});
