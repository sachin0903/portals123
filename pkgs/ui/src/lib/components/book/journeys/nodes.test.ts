/**
 * The v3 node vocabulary against the contract, and the legacy upgrade against the shape the old
 * editor drew. These are the assertions the builder is allowed to rely on.
 */
import { describe, it, expect } from 'vitest';
import contract from './flow-contract.json' with { type: 'json' };
import {
	NODE_KINDS,
	NODE_KIND_LABEL,
	NODE_KIND_HINT,
	CONTROL_CONFIG_KEYS,
	toNodes,
	normaliseNode,
	outgoing,
	branchesOf,
	isGraphDefinition,
	type FlowNode
} from './nodes.js';
import type { FlowDefinition, FlowStep } from './flows.js';

const step = (id: string, over: Partial<FlowStep> = {}): FlowStep => ({
	id,
	type: 'task',
	title: id,
	required: true,
	assignee: 'staff',
	scope: 'application',
	config: {},
	...over
});

describe('the kind vocabulary tracks the contract', () => {
	it('lists exactly the contract kinds', () => {
		expect([...NODE_KINDS]).toEqual(contract.nodeKinds);
	});

	it('labels and describes every one of them', () => {
		for (const kind of contract.nodeKinds) {
			expect(NODE_KIND_LABEL[kind as keyof typeof NODE_KIND_LABEL]).toBeTruthy();
			expect(NODE_KIND_HINT[kind as keyof typeof NODE_KIND_HINT]).toBeTruthy();
		}
	});

	it('carries each control kind s config keys', () => {
		for (const [kind, spec] of Object.entries(contract.controlNodes)) {
			expect(CONTROL_CONFIG_KEYS[kind]).toEqual((spec as { config: string[] }).config);
		}
	});

	it('has a control entry for every non-work kind', () => {
		const control = NODE_KINDS.filter((k) => k !== 'step' && k !== 'action');
		expect(control.sort()).toEqual(Object.keys(contract.controlNodes).sort());
	});
});

describe('reading a legacy definition', () => {
	const legacy: FlowDefinition = {
		steps: [step('a'), step('b'), step('c')],
		milestones: []
	};

	it('is not mistaken for a graph', () => {
		expect(isGraphDefinition(legacy)).toBe(false);
	});

	it('invents NO edges and NO start node — a legacy document has neither', () => {
		// Twice now this read has been "helpfully" made faithful to something. The order the pre-v3
		// editor drew was cosmetic, so compiling it into `next` turned independent steps into a chain
		// of hard prerequisites (`graph.effective_depends_on`). Fanning a minted start node out to
		// every dependency-free step then told the opposite lie: the canvas announced "13 paths at
		// once" for a journey whose steps are spread across three TIERS and cannot all start at once.
		//
		// A legacy document has no edges. The read says so. Wires, and the start node they hang off,
		// arrive only when an author converts (`convert.ts::convertLegacy`).
		const nodes = toNodes(legacy);
		expect(nodes.map((n) => [n.id, n.next])).toEqual([
			['a', null],
			['b', null],
			['c', null]
		]);
	});

	it('mints no start node', () => {
		expect(toNodes(legacy).map((n) => n.id)).toEqual(['a', 'b', 'c']);
		expect(toNodes(legacy).some((n) => n.kind === 'start')).toBe(false);
	});

	it('keeps depends_on verbatim — the ordering the engine actually reads', () => {
		const nodes = toNodes({
			steps: [step('a'), step('b', { depends_on: ['a'] })],
			milestones: []
		});
		expect(nodes.map((n) => n.depends_on)).toEqual([[], ['a']]);
	});

	it('makes every step a step node and keeps what it carried', () => {
		const nodes = toNodes({
			steps: [step('a', { scope: 'participant', depends_on: ['z'], config: { description: 'x' } })],
			milestones: []
		});
		expect(nodes[0].kind).toBe('step');
		expect(nodes[0].scope).toBe('participant');
		// A dependency naming a step that is not in the document is kept verbatim: the validator
		// refuses one, and dropping it here would hide the problem. (At runtime the two readings agree
		// anyway — `dependencies.unmet` treats a prerequisite that was never created as satisfied.)
		expect(nodes[0].depends_on).toEqual(['z']);
		expect(nodes[0].next).toBeNull();
		expect(nodes[0].config).toEqual({ description: 'x' });
	});

	it('reads an empty definition as no nodes rather than throwing', () => {
		expect(toNodes(null)).toEqual([]);
		expect(toNodes({ steps: [], milestones: [] })).toEqual([]);
	});
});

describe('the node envelope', () => {
	it('defaults a missing kind to step, so an un-migrated node loads', () => {
		expect(normaliseNode({ id: 'a' }).kind).toBe('step');
	});

	it('drops a stray next off a branch and off a terminal — graph.parse refuses both', () => {
		expect(normaliseNode({ id: 'b', kind: 'branch', next: 'x' }).next).toBeUndefined();
		expect(normaliseNode({ id: 'e', kind: 'exit', next: 'x' }).next).toBeUndefined();
		expect(normaliseNode({ id: 'f', kind: 'start_flow', next: 'x' }).next).toBeUndefined();
	});

	it('keeps a null next on an unfinished work node rather than dropping the key', () => {
		const node = normaliseNode({ id: 'a', kind: 'step' });
		expect('next' in node).toBe(true);
		expect(node.next).toBeNull();
	});

	it('defaults position to null — unplaced, for the layout pass to place', () => {
		expect(normaliseNode({ id: 'a' }).position).toBeNull();
		expect(normaliseNode({ id: 'a', position: { x: 4, y: 8 } }).position).toEqual({ x: 4, y: 8 });
	});
});

describe('following edges', () => {
	const branch: FlowNode = normaliseNode({
		id: 'br',
		kind: 'branch',
		config: {
			branches: [
				{
					id: '1',
					label: 'Advised',
					conditions: { field: 'x', operator: 'equals', value: 1 },
					next: 'a'
				},
				{ id: '2', label: 'Otherwise', conditions: null, next: 'b' }
			]
		}
	});

	it('follows a work node s single edge', () => {
		expect(outgoing(normaliseNode({ id: 'a', next: 'b' }))).toEqual(['b']);
		expect(outgoing(normaliseNode({ id: 'a' }))).toEqual([]);
	});

	it('follows every arm of a branch', () => {
		expect(outgoing(branch)).toEqual(['a', 'b']);
		expect(branchesOf(branch)).toHaveLength(2);
	});

	it('skips an arm that is not wired up yet', () => {
		const half = normaliseNode({
			id: 'br',
			kind: 'branch',
			config: { branches: [{ id: '1', label: 'a', conditions: null, next: null }] }
		});
		expect(outgoing(half)).toEqual([]);
	});

	it('follows nothing out of a terminal', () => {
		expect(outgoing(normaliseNode({ id: 'e', kind: 'exit' }))).toEqual([]);
		expect(outgoing(normaliseNode({ id: 's', kind: 'start_flow' }))).toEqual([]);
	});

	it('reads no arms off a node that is not a branch', () => {
		expect(branchesOf(normaliseNode({ id: 'a', config: { branches: [{}] } }))).toEqual([]);
	});
});
