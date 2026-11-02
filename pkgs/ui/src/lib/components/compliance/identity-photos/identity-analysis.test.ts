import { describe, expect, it } from 'vitest';
import { OUTCOME_STYLES, hasAnalysis, outcomeKind, scorePercent } from './identity-analysis';

const EMPTY = { groups: [], scores: [], facts: [], links: [] };

describe('outcomeKind', () => {
	it('reads each outcome as clear, needs attention, failed or not processed', () => {
		expect(outcomeKind('clear')).toBe('clear');
		expect(outcomeKind('Passed')).toBe('clear');
		expect(outcomeKind('attention')).toBe('attention');
		expect(outcomeKind('refer')).toBe('attention');
		expect(outcomeKind('consider')).toBe('attention');
		expect(outcomeKind('rejected')).toBe('rejected');
		expect(outcomeKind('FAILED')).toBe('rejected');
		expect(outcomeKind('not_clear')).toBe('rejected');
		expect(outcomeKind('not_processed')).toBe('none');
		expect(outcomeKind('')).toBe('none');
	});

	it('has a word for each kind, in legend order', () => {
		expect(Object.values(OUTCOME_STYLES).map((o) => o.word)).toEqual([
			'Clear',
			'Needs attention',
			'Failed',
			'Not processed'
		]);
	});
});

describe('scorePercent', () => {
	it('reads a score given as 0–100 or as a fraction of 1', () => {
		expect(scorePercent(83)).toBe('83%');
		expect(scorePercent(0.83)).toBe('83%');
		expect(scorePercent(0.5)).toBe('50%');
		expect(scorePercent(0)).toBe('0%');
		expect(scorePercent(1)).toBe('1%');
		expect(scorePercent(99.6)).toBe('100%');
	});
});

describe('hasAnalysis', () => {
	it('is true only when there is an outcome, a score or a fact to show', () => {
		expect(hasAnalysis(EMPTY)).toBe(false);
		expect(
			hasAnalysis({ ...EMPTY, links: [{ key: 'l', label: 'Specimen', urls: ['https://x'] }] })
		).toBe(false);
		expect(hasAnalysis({ ...EMPTY, scores: [{ key: 's', label: 'Liveness', value: 83 }] })).toBe(
			true
		);
		expect(hasAnalysis({ ...EMPTY, facts: [{ key: 'f', label: 'Side', value: 'front' }] })).toBe(
			true
		);
		expect(
			hasAnalysis({
				...EMPTY,
				groups: [
					{ key: 'g', label: 'Face', items: [{ key: 'i', label: 'Match', outcome: 'clear' }] }
				]
			})
		).toBe(true);
	});
});
