/**
 * The ONE next instruction for a run — the transfers panel's `nextStep` rule, applied to a baton
 * run. A screen offers one primary act (decide, or retry from the node that stopped) and names
 * what it will do; Cancel is always secondary. Today's detail page had a Retry on every failed
 * step and nothing on the run; this is the single place that decides instead.
 */
import type { RunDetailData } from './run-types.js';

export type NextActionKind = 'decide' | 'retry' | 'none';

export interface NextAction {
	kind: NextActionKind;
	/** The button's words: "Retry from Attach SEI model", "Approve" (with Reject beside it). */
	label: string;
	/** The sentence beside it saying what the act does, or why there is none. */
	hint: string;
	/** Whether Cancel is offered as the secondary act. */
	canCancel: boolean;
}

export function nextAction(run: RunDetailData): NextAction {
	const canCancel = run.can_cancel;
	if (run.status === 'awaiting_approval') {
		if (run.approval.can_decide) {
			return {
				kind: 'decide',
				label: 'Approve',
				hint: run.approval.roles.length
					? `Held for ${run.approval.roles.join(' or ')}. Approving runs it now.`
					: 'Held for a second person. Approving runs it now.',
				canCancel
			};
		}
		return {
			kind: 'none',
			label: '',
			hint: run.approval.roles.length
				? `Waiting for ${run.approval.roles.join(' or ')} to decide.`
				: 'Waiting for a second person to decide. You cannot decide this one.',
			canCancel
		};
	}
	if (run.can_retry) {
		const node = run.attention?.node_name;
		// The button says "Retry"; the hint beside it says what that re-runs.
		return {
			kind: 'retry',
			label: 'Retry',
			hint: node
				? `Re-runs ${node} and nothing before it. What already went through is kept.`
				: 'Re-runs what stopped. What already went through is kept.',
			canCancel
		};
	}
	if (run.status === 'waiting') {
		return {
			kind: 'none',
			label: '',
			hint: 'Resting on the provider; it polls again on its own.',
			canCancel
		};
	}
	if (run.status === 'running') {
		return { kind: 'none', label: '', hint: 'Running. This page refreshes itself.', canCancel };
	}
	return { kind: 'none', label: '', hint: '', canCancel };
}
