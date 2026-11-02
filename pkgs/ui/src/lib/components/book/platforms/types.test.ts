/**
 * How current a data source is — the rule the firm's badge reads.
 *
 * The distinction that matters is `never` versus `stale`, and it is not cosmetic. "Nothing has
 * ever landed for this firm" is a SETUP problem someone has to be told about: a connection enabled
 * without an active storage row registers no models on the daemon at all and runs silently for
 * ever. "Stale" is a run that has not happened lately. Collapsing them is how this pipeline's
 * whole class of silent failure hides.
 */
import { describe, expect, it } from 'vitest';
import { dataSourceState, type DataSource } from './types';

const NOW = new Date('2026-08-10T09:00:00Z');

const source = (over: Partial<DataSource> = {}): DataSource => ({
	provider: 'seccl',
	name: 'Seccl',
	role: 'custodian',
	description: '',
	transports: ['api'],
	last_merged_at: '2026-08-10T02:00:00Z',
	last_run: { run_at: '2026-08-10T02:00:00Z', created: 10, updated: 2, rejected: 0, failed: 0 },
	...over
});

describe('dataSourceState', () => {
	it('is current after last night’s run', () => {
		expect(dataSourceState(source(), NOW)).toBe('current');
	});

	it('is stale once a daily sync has plainly been missed', () => {
		// Every custodian here syncs at least daily, so more than a day and a half without one is
		// a fact worth surfacing rather than a rounding error.
		expect(dataSourceState(source({ last_merged_at: '2026-08-08T02:00:00Z' }), NOW)).toBe('stale');
	});

	it('reports NEVER separately from stale', () => {
		// The setup failure: enabled, green, and invisible to the pipeline. It must not read as
		// "we are a bit behind".
		expect(dataSourceState(source({ last_merged_at: null, last_run: null }), NOW)).toBe('never');
	});

	it('reports partial when the last run left entities unmerged', () => {
		// One entity failing does not stop the walk — so the book is INCOMPLETE rather than
		// missing, and "up to date" would be a lie about data an adviser is reading.
		const partial = source({
			last_run: { run_at: '2026-08-10T02:00:00Z', created: 10, updated: 0, rejected: 0, failed: 1 }
		});
		expect(dataSourceState(partial, NOW)).toBe('partial');
	});

	it('prefers partial over freshness', () => {
		// A run that completed minutes ago and dropped an entity is not "up to date".
		const partial = source({
			last_merged_at: NOW.toISOString(),
			last_run: { run_at: NOW.toISOString(), created: 1, updated: 0, rejected: 0, failed: 2 }
		});
		expect(dataSourceState(partial, NOW)).toBe('partial');
	});

	it('does not treat refused ROWS as a partial run', () => {
		// A reject is a row the merge refused for a stated reason; the run itself completed. The
		// firm is told the count separately — conflating the two would make every Seccl book
		// permanently amber over 117 known-unmapped transaction types.
		const rejected = source({
			last_run: {
				run_at: '2026-08-10T02:00:00Z',
				created: 10,
				updated: 0,
				rejected: 117,
				failed: 0
			}
		});
		expect(dataSourceState(rejected, NOW)).toBe('current');
	});
});
