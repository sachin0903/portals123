/**
 * SSR coverage for the per-kind inspectors.
 *
 * What SSR can pin here is exactly what matters about these screens:
 *
 *  - **the dispatcher routes on `kind`** and falls back to the raw editor rather than rendering
 *    nothing for a kind nobody has written an inspector for yet;
 *  - **the copy that carries the semantics is on the page** — a wait's plain-English sentence, the
 *    hand-over's "this journey ends here", the branch's numbered arms and its mandatory fallback,
 *    the action's permission line. Every one of those is a decision an author would otherwise have
 *    to infer, and each is a thing a well-meaning tidy-up would delete;
 *  - **read-only means read-only**: `canManage: false` renders no add/remove control;
 *  - **a trigger nothing emits is offered but disabled**, not hidden and not silently broken.
 *
 * Interaction (typing, reordering, re-pointing an edge) is client-side and out of SSR's reach; the
 * pure rules behind it are unit-tested in `shared.test.ts`.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { conditionArms, type FlowNode } from '@graphene/ui/book/journeys';
import NodeInspector from './node-inspector.svelte';
import type { InspectorProps } from './types';

const node = (over: Partial<FlowNode> & { id: string; kind: FlowNode['kind'] }): FlowNode => ({
	title: '',
	config: {},
	position: null,
	depends_on: [],
	...over
});

function props(target: FlowNode, over: Partial<InspectorProps> = {}): InspectorProps {
	return {
		node: target,
		nodes: [target, node({ id: 'chase', kind: 'action', title: 'Chase them' })],
		forms: [],
		documents: [],
		templates: [],
		roles: [],
		flows: [],
		canManage: true,
		onchange: () => {},
		onrestructure: () => {},
		...over
	};
}

const html = (target: FlowNode, over: Partial<InspectorProps> = {}) =>
	render(NodeInspector, { props: props(target, over) }).body;

describe('the dispatcher', () => {
	it('sends each kind to its own inspector', () => {
		expect(html(node({ id: 'w', kind: 'wait' }))).toContain('What this wait is for');
		expect(html(node({ id: 'e', kind: 'exit' }))).toContain('The journey ends as');
		expect(html(node({ id: 's', kind: 'start_flow' }))).toContain('This journey ends here');
		expect(html(node({ id: 'a', kind: 'action' }))).toContain('The engine will');
		expect(html(node({ id: 'f', kind: 'wait_for_event' }))).toContain('What this is waiting for');
		expect(html(node({ id: 'c', kind: 'condition' }))).toContain('What this decides');
	});

	it('falls back to the raw editor for a kind with no inspector', () => {
		// `step` is StepInspector's, so the dispatcher never sees it — but a future kind would land
		// here rather than rendering an empty panel.
		expect(html(node({ id: 'x', kind: 'step' }))).toContain('Raw settings for this node');
	});

	it('shows this node’s publish issues at the top', () => {
		const out = html(node({ id: 'w', kind: 'wait' }), {
			issues: ['Wait “Cooling off” does not say how long to wait.']
		});
		expect(out).toContain('does not say how long to wait');
	});
});

describe('the wait inspector', () => {
	it('says in words what the numbers mean', () => {
		expect(html(node({ id: 'w', kind: 'wait', config: { duration_days: 3 } }))).toContain(
			'Pauses for 3 days, then continues.'
		);
	});

	it('warns before publish does when the wait is over the cap', () => {
		const out = html(node({ id: 'w', kind: 'wait', config: { duration_days: 400 } }));
		expect(out).toContain('will not publish');
		expect(out).toContain('scheduled automation');
	});
});

describe('the branch inspector', () => {
	const branch = () =>
		node({
			id: 'fork',
			kind: 'branch',
			title: 'Advised?',
			config: {
				branches: [
					{
						id: 'b1',
						label: 'Advised',
						conditions: { field: 'x', operator: 'equals' },
						next: null
					},
					{ id: 'b2', label: 'Otherwise', conditions: null, next: 'chase' }
				]
			}
		});

	it('numbers the arms so first-match-wins is visible', () => {
		const out = html(branch());
		expect(out).toContain('1st · if');
		expect(out).toContain('Otherwise');
	});

	it('explains the fallback rather than leaving it as an unlabelled row', () => {
		expect(html(branch())).toContain('matched none of the paths above');
	});

	it('offers no add or remove control to a reader', () => {
		const out = html(branch(), { canManage: false });
		expect(out).not.toContain('Add a path');
		expect(out).not.toContain('Remove path');
	});

	it('flags an arm that leads nowhere', () => {
		expect(html(branch())).toContain('This path leads nowhere yet');
	});
});

describe('the condition inspector', () => {
	const gate = (arms: { when?: string | null; otherwise?: string | null } = {}) =>
		node({
			id: 'only_individuals',
			kind: 'condition',
			title: 'Individuals only',
			config: {
				branches: conditionArms(
					{ field: 'contact.type', operator: 'equals', value: 'individual' },
					{ when: 'chase', otherwise: 'chase', ...arms }
				)
			}
		});

	it('edits the condition and BOTH arm targets', () => {
		// The two pickers are the whole panel: a condition that cannot say where its "no" answer goes
		// is a condition that silently ends the journey for half the cases.
		const out = html(gate());
		expect(out).toContain('Only when');
		expect(out).toContain('If it holds, go to');
		expect(out).toContain('Otherwise, go to');
	});

	it('says so when both arms lead to the same place — the convergence', () => {
		// THE thing an author needs told: the commonest answer is "otherwise, carry on", and they must
		// not have to work out that they are allowed to give it.
		const out = html(gate());
		expect(out).toContain('Both paths lead to the same place');
		expect(out).toContain('Chase them');
	});

	it('warns when the second path leads nowhere, because the journey would stop there', () => {
		expect(html(gate({ otherwise: null }))).toContain('a case that does not match stops here');
	});

	it('points at a branch for a third answer rather than growing into one', () => {
		expect(html(gate())).toContain('use a branch');
	});

	it('renders read-only without an editing control', () => {
		const out = html(gate(), { canManage: false });
		expect(out).toContain('Individuals only');
	});
});

describe('the wait-for-event inspector', () => {
	it('says so when the chosen event could never release the case', () => {
		// `case.completed` is domain-sourced: authorable as a RULE trigger, undeliverable to a
		// parked case (`listeners.deliverableSources`). The picker offers it disabled — the options
		// themselves live in a closed popover SSR does not render, so what is pinned here is the
		// consequence an author has to see.
		const out = html(
			node({ id: 'f', kind: 'wait_for_event', config: { trigger: 'case.completed' } })
		);
		expect(out).toContain('Can’t be waited for');
		expect(out).toContain('nothing delivers this event yet');
	});

	it('says so for a SCHEDULE, which the rule-tier predicate called live', () => {
		// The regression that mattered: `isSupported('schedule.recurring')` is true, so the picker
		// offered it enabled and publish said nothing — and a schedule can never release a listener,
		// so the case wedged permanently.
		const out = html(
			node({ id: 'f', kind: 'wait_for_event', config: { trigger: 'schedule.recurring' } })
		);
		expect(out).toContain('Can’t be waited for');
		expect(out).toContain('a schedule isn’t an event');
	});

	it('says so for a task trigger — a journey has no task of its own', () => {
		const out = html(
			node({ id: 'f', kind: 'wait_for_event', config: { trigger: 'task.completed' } })
		);
		expect(out).toContain('Can’t be waited for');
		expect(out).toContain('never about a task');
	});

	it('leaves a deliverable trigger alone', () => {
		const out = html(
			node({ id: 'f', kind: 'wait_for_event', config: { trigger: 'client.form_submitted' } })
		);
		expect(out).not.toContain('Can’t be waited for');
		expect(out).toContain('Fires for the journey’s contact');
	});

	it('warns about waiting for ever when there is no deadline', () => {
		expect(html(node({ id: 'f', kind: 'wait_for_event' }))).toContain('waits indefinitely');
	});

	it('asks where a timed-out case goes once a deadline is set', () => {
		const out = html(
			node({
				id: 'f',
				kind: 'wait_for_event',
				config: { trigger: 'client.form_submitted', timeout_days: 7 }
			})
		);
		expect(out).toContain('If it hasn’t happened in');
		expect(out).toContain('Pick where a case goes when the deadline passes');
	});

	it('refuses a stored trigger the engine no longer knows, in words', () => {
		const out = html(node({ id: 'f', kind: 'wait_for_event', config: { trigger: 'made.up' } }));
		expect(out).toContain('isn’t an event this engine knows');
	});
});

describe('the hand-over inspector', () => {
	it('states the tail-call semantics rather than implying them', () => {
		const out = html(node({ id: 's', kind: 'start_flow' }));
		expect(out).toContain('control never comes back');
		expect(out).not.toContain('Then go to'); // the engine refuses a `next` on this kind
	});

	it('degrades to a free-text code when there are no other journeys yet', () => {
		expect(html(node({ id: 's', kind: 'start_flow' }))).toContain('No other journeys to pick from');
	});
});

describe('the exit inspector', () => {
	it('says what each outcome means for anything listening', () => {
		expect(html(node({ id: 'e', kind: 'exit' }))).toContain('a journey completes');
	});
});

describe('the action inspector', () => {
	it('surfaces the authority the action asserts', () => {
		const out = html(
			node({ id: 'a', kind: 'action', type: 'send_email' } as Partial<FlowNode> & {
				id: string;
				kind: 'action';
			})
		);
		expect(out).toContain('documents:send');
		expect(out).toContain('the same authority a person needs');
	});

	it('renders the contract’s own config fields', () => {
		const out = html(
			node({ id: 'a', kind: 'action', type: 'raise_task' } as Partial<FlowNode> & {
				id: string;
				kind: 'action';
			})
		);
		expect(out).toContain('Due in (days)');
		expect(out).toContain('Or assign to role');
	});

	it('says nobody is assigned it', () => {
		expect(
			html(
				node({ id: 'a', kind: 'action', type: 'apply_tag' } as Partial<FlowNode> & {
					id: string;
					kind: 'action';
				})
			)
		).toContain('Nobody is assigned this');
	});
});
