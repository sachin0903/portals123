/** The contribution lifecycle vocabulary, and the one rule the UI must not get wrong.
 *
 * `cam_updatecontributionapplication` is valid only while Morgan Lloyd has the record at
 * Application (809130000). The API re-checks with the vendor before sending — the mirror is
 * only as fresh as the last refresh — but offering a control that is going to 409 is its own
 * kind of wrong, so the constant that gates it is pinned here against the option-set values.
 *
 * The Python side pins the same numbers against `morgan_lloyd.enums.ContributionStatus`
 * (`tests/test_morgan_lloyd_contributions.py`). Two languages, one vocabulary, both tested —
 * the same discipline the relief maths already gets.
 */
import { describe, expect, it } from 'vitest';
import {
	CLASSIFIABLE,
	ML_AMENDABLE_STATUS,
	ML_STATUS_TONE,
	NEEDS_ACTION,
	METRIC_STAGES,
	OFF_PIPELINE,
	RELIEF_CLAIMED,
	STAGES,
	STATUSES,
	STATUS_LABEL,
	STATUS_TONE,
	stageOf
} from './status.js';
import { ML_STATUS, ML_STATUS_LABELS, type ContributionStatus } from './types.js';

const ALL: ContributionStatus[] = [
	'identified',
	'awaiting_confirmation',
	'confirmed',
	'ml_recorded',
	'ras_applied',
	'ras_received',
	'tax_relief_received',
	'reconciled',
	'manual_intervention',
	'cancelled'
];

describe('the Morgan Lloyd status mirror', () => {
	it('carries the four documented option-set values', () => {
		expect(ML_STATUS).toEqual({
			application: 809130000,
			active: 809130001,
			stopped: 809130002,
			received: 809130003
		});
	});

	it('gates amendment on Application and nothing else', () => {
		expect(ML_AMENDABLE_STATUS).toBe(ML_STATUS.application);
		for (const status of [ML_STATUS.active, ML_STATUS.stopped, ML_STATUS.received]) {
			expect(status).not.toBe(ML_AMENDABLE_STATUS);
		}
	});

	it('labels and tones all four', () => {
		for (const value of Object.values(ML_STATUS)) {
			expect(ML_STATUS_LABELS[value]).toBeTruthy();
			expect(ML_STATUS_TONE[value]).toBeTruthy();
		}
	});
});

describe('the pipeline', () => {
	it('holds the eight forward stages, terminals excluded', () => {
		expect(STATUSES).toEqual([
			'identified',
			'awaiting_confirmation',
			'confirmed',
			'ml_recorded',
			'ras_applied',
			'ras_received',
			'tax_relief_received',
			'reconciled'
		]);
		expect(STATUSES).not.toContain('cancelled');
		expect(STATUSES).not.toContain('manual_intervention');
	});

	it('treats cancelled as a terminal, not as stage nine', () => {
		// A cancelled contribution is a way the journey stops. Appending it to the strip would
		// read as progress, and counting it would make a tidy book look busy.
		expect(OFF_PIPELINE.has('cancelled')).toBe(true);
		expect(OFF_PIPELINE.has('manual_intervention')).toBe(true);
	});

	it('labels and tones every status the API can return', () => {
		for (const status of ALL) {
			expect(STATUS_LABEL[status]).toBeTruthy();
			expect(STATUS_TONE[status]).toBeTruthy();
		}
	});

	it('uses only semantic tokens for tone', () => {
		for (const tone of Object.values(STATUS_TONE)) {
			expect(tone).not.toMatch(/\b(?:text|bg|border)-(?:red|green|amber|blue|slate|gray)-\d/);
		}
	});

	it('gives every status a stage — including the terminals', () => {
		// TOTAL, not merely covering the forward stages. `stageOf` drives the badge, the metrics
		// and the status filter alike, so a status with no stage would render an empty badge, be
		// counted by no metric and be unreachable by any filter option — three silent absences
		// from one omission.
		for (const status of ALL) expect(stageOf(status), status).not.toBeNull();
	});

	it('claims each status exactly once across the stages', () => {
		// Two stages holding one status would double-count the money: the KPI strip sums a stage's
		// rows, and a `confirmed` row in both "To classify" and "Not lodged" is £500
		// reported as £1,000 across the strip.
		const seen = new Set<string>();
		for (const stage of STAGES) {
			for (const status of stage.statuses) {
				expect(seen.has(status), status).toBe(false);
				seen.add(status);
			}
		}
	});

	it('shows the six metrics and keeps the terminals to the filter', () => {
		expect(METRIC_STAGES.map((stage) => stage.key)).toEqual([
			'awaiting_classification',
			'with_administrator',
			'relief_claimed',
			'relief_in_transit',
			'relief_credited',
			'not_lodged'
		]);
		// Completed and Cancelled are filter answers, not work: counting a finished contribution
		// makes a tidy book look busy, which is the same reason `OFF_PIPELINE` exists.
		expect(STAGES.filter((stage) => stage.metric === false).map((stage) => stage.key)).toEqual([
			'completed',
			'cancelled'
		]);
	});

	it('names an actor and a unit for every stage', () => {
		// The caption under each figure is the whole point of the strip — half these stages are
		// waiting on somebody else, and an adviser needs to know which half is theirs.
		for (const stage of STAGES) {
			expect(stage.actor, stage.key).toBeTruthy();
			expect(['transactions', 'contributions']).toContain(stage.unit);
		}
	});

	it('does not colour a cancellation as a failure', () => {
		// Cancelling is usually a correct decision — a client changed their mind, or a credit was
		// not a contribution. Destructive styling would make routine tidying look alarming.
		expect(STATUS_TONE.cancelled).toBe('neutral');
		expect(STATUS_TONE.manual_intervention).toBe('danger');
	});
});

describe('what the panel may offer', () => {
	it('allows reclassification only up to confirmed', () => {
		expect([...CLASSIFIABLE].sort()).toEqual(['awaiting_confirmation', 'confirmed', 'identified']);
		// Past confirmed the API answers 409; the panel must not invite the attempt.
		for (const status of ['ml_recorded', 'ras_applied', 'reconciled'] as const) {
			expect(CLASSIFIABLE.has(status)).toBe(false);
		}
	});

	it('hides cancel once a reclaim is in flight at HMRC', () => {
		for (const status of [
			'ras_applied',
			'ras_received',
			'tax_relief_received',
			'reconciled'
		] as const) {
			expect(RELIEF_CLAIMED.has(status)).toBe(true);
		}
		for (const status of [
			'identified',
			'awaiting_confirmation',
			'confirmed',
			'ml_recorded'
		] as const) {
			expect(RELIEF_CLAIMED.has(status)).toBe(false);
		}
	});

	it('counts only work a human has to do as needing action', () => {
		expect(NEEDS_ACTION.has('awaiting_confirmation')).toBe(true);
		expect(NEEDS_ACTION.has('manual_intervention')).toBe(true);
		// Waiting on Morgan Lloyd or HMRC is not the adviser's action.
		expect(NEEDS_ACTION.has('ml_recorded')).toBe(false);
		expect(NEEDS_ACTION.has('ras_applied')).toBe(false);
		expect(NEEDS_ACTION.has('cancelled')).toBe(false);
	});
});
