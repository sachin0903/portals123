import { describe, expect, it } from 'vitest';
import { activeJourneys, type ClientJourneySummary } from './client-journey';

function journey(status: string): ClientJourneySummary {
	return {
		uid: status,
		status,
		flow_name: 'Fact find',
		flow_code: 'fact_find',
		created_at: '2026-09-03',
		steps_done: 1,
		steps_total: 4
	};
}

describe('activeJourneys', () => {
	it('keeps a journey still in flight and drops one that has finished', () => {
		const kept = activeJourneys([
			journey('active'),
			journey('waiting'),
			journey('blocked'),
			journey('draft'),
			journey('completed'),
			journey('cancelled'),
			journey('failed')
		]);
		expect(kept.map((j) => j.status)).toEqual(['active', 'waiting', 'blocked', 'draft']);
	});
});
