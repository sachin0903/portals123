/**
 * The one-line summaries the listing lives on.
 *
 * These are read far more often than the builder is opened — a firm scans this column to
 * understand what its machine does — so a sentence that misreads a rule is worse than no sentence.
 * The polarity cases matter most: "is not" rendered as "is" inverts the meaning of the rule while
 * looking perfectly plausible.
 */
import { describe, expect, it } from 'vitest';
import {
	fieldLabel,
	formatWhen,
	outcomeHint,
	outcomeLabel,
	outcomeTone,
	summariseConditions,
	summariseRearm,
	summariseSchedule,
	summariseTarget
} from './summary.js';

describe('fieldLabel', () => {
	it('reads a scoped field in the firm’s vocabulary', () => {
		expect(fieldLabel('contact.service_type')).toBe('Client service type');
		expect(fieldLabel('account.value')).toBe('Account value');
		expect(fieldLabel('case.flow_code')).toBe('Journey flow code');
	});

	it('keeps an authored memory key readable', () => {
		expect(fieldLabel('memory.chase_count')).toBe('Memory chase count');
	});

	it('degrades rather than blanking', () => {
		expect(fieldLabel('')).toBe('Something');
		expect(fieldLabel('legacy_key')).toBe('Legacy key');
	});
});

describe('summariseConditions', () => {
	it('says "Always" for no conditions — the truth, not a blank cell', () => {
		expect(summariseConditions(null)).toBe('Always');
		expect(summariseConditions({})).toBe('Always');
	});

	it('renders a leaf as a clause', () => {
		expect(
			summariseConditions({ field: 'contact.type', operator: 'equals', value: 'individual' })
		).toBe('Client type is individual');
	});

	it('renders POLARITY — the case that inverts a rule if it goes wrong', () => {
		expect(
			summariseConditions({ field: 'contact.type', operator: 'notEquals', value: 'trust' })
		).toBe('Client type is not trust');
		expect(
			summariseConditions({ field: 'contact.tags', operator: 'notContains', value: 'vip' })
		).toBe('Client tags doesn’t contain vip');
		expect(summariseConditions({ field: 'contact.nationality', operator: 'isNotEmpty' })).toBe(
			'Client nationality is not empty'
		);
		expect(
			summariseConditions({ field: 'case.started_at', operator: 'olderThanDays', value: 30 })
		).toBe('Journey started at is older than 30 days');
	});

	it('lists an "is one of" value readably', () => {
		expect(
			summariseConditions({ field: 'contact.type', operator: 'in', value: ['individual', 'trust'] })
		).toBe('Client type is one of individual or trust');
	});

	it('joins a group with its own logic word', () => {
		expect(
			summariseConditions({
				logic: 'or',
				conditions: [
					{ field: 'contact.type', operator: 'equals', value: 'individual' },
					{ field: 'contact.enabled', operator: 'equals', value: true }
				]
			})
		).toBe('Client type is individual or Client enabled is true');
	});

	it('parenthesises a NESTED group only, so the top level reads as a sentence', () => {
		expect(
			summariseConditions({
				logic: 'and',
				conditions: [
					{ field: 'contact.type', operator: 'equals', value: 'individual' },
					{
						logic: 'or',
						conditions: [
							{ field: 'contact.tags', operator: 'contains', value: 'vip' },
							{ field: 'account.value', operator: 'greaterThan', value: 100000 }
						]
					}
				]
			})
		).toBe(
			'Client type is individual and (Client tags contains vip or Account value is more than 100000)'
		);
	});

	it('resolves a field-reference value to its own label', () => {
		expect(
			summariseConditions({
				field: 'case.started_at',
				operator: 'before',
				value: { field: 'contact.created_at' }
			})
		).toBe('Journey started at is before Client created at');
	});

	it('renders an unknown operator without pretending to understand it', () => {
		expect(summariseConditions({ field: 'a', operator: 'teleports', value: 'x' })).toBe(
			'A teleports x'
		);
	});
});

describe('summariseTarget', () => {
	it('names the journey a rule starts', () => {
		expect(
			summariseTarget({ flow_name: 'Onboarding', flow_code: 'onboarding', action: null })
		).toBe('Start “Onboarding”');
	});

	it('names the action and its most telling setting', () => {
		expect(
			summariseTarget({
				flow_name: null,
				flow_code: null,
				action: { type: 'apply_tag', config: { tag: 'chase-sent' } }
			})
		).toBe('Apply a tag: chase-sent');
		expect(
			summariseTarget({
				flow_name: null,
				flow_code: null,
				action: { type: 'raise_task', config: { title: 'Call the client' } }
			})
		).toBe('Raise a task: Call the client');
	});

	it('falls back to the action label alone when the setting is blank', () => {
		expect(
			summariseTarget({
				flow_name: null,
				flow_code: null,
				action: { type: 'apply_tag', config: {} }
			})
		).toBe('Apply a tag');
	});

	it('tells the truth about a targetless row rather than rendering blank', () => {
		// The database forbids this shape; the UI still has to say something honest if it sees one.
		expect(summariseTarget({ flow_name: null, flow_code: null, action: null })).toBe('Nothing');
	});
});

describe('summariseSchedule', () => {
	it('reads the common expressions in English', () => {
		expect(summariseSchedule({ cron: '0 9 * * 1-5' })).toBe('every weekday at 09:00');
		expect(summariseSchedule({ cron: '30 7 * * *' })).toBe('every day at 07:30');
	});

	it('falls back to the raw expression rather than guessing', () => {
		expect(summariseSchedule({ cron: '0 6-18/6 * * 1-5' })).toBe('0 6-18/6 * * 1-5');
		expect(summariseSchedule({ cron: '0 9 1 * *' })).toBe('0 9 1 * *');
	});

	it('is empty for no schedule', () => {
		expect(summariseSchedule(null)).toBe('');
	});
});

describe('summariseRearm', () => {
	it('reads a whole number of days or hours', () => {
		expect(summariseRearm(1440, 1440)).toContain('1 day');
		expect(summariseRearm(120, 1440)).toContain('2 hours');
		expect(summariseRearm(90, 1440)).toContain('90 minutes');
	});

	it('uses the firm default when the rule has none', () => {
		expect(summariseRearm(null, 2880)).toContain('2 days');
	});

	it('says so when there is no window at all', () => {
		expect(summariseRearm(0, 1440)).toBe('Can re-fire immediately.');
	});
});

describe('the run-log vocabulary', () => {
	it('gives every stored outcome real copy, not a de-slugged enum', () => {
		// The CHECK constraint's full set (0043). "Why didn't it fire?" is the question the log
		// exists to answer, and `skipped_rearm` answers none of it.
		const outcomes = [
			'dispatched',
			'deferred',
			'skipped_conditions',
			'skipped_rearm',
			'skipped_paused',
			'skipped_automation',
			'budget_exhausted',
			'depth_exceeded',
			'failed'
		];
		for (const outcome of outcomes) {
			expect(outcomeLabel(outcome)).not.toContain('_');
			expect(outcomeHint(outcome)).toBeTruthy();
		}
		expect(outcomeTone('dispatched')).toBe('success');
		expect(outcomeTone('failed')).toBe('danger');
		expect(outcomeTone('skipped_conditions')).toBe('neutral');
	});

	it('reads a never-run automation as never run', () => {
		expect(outcomeLabel(null)).toBe('Never run');
		expect(outcomeTone(null)).toBe('neutral');
	});

	it('degrades for an outcome this build has never heard of', () => {
		expect(outcomeLabel('invented_outcome')).toBe('Invented outcome');
		expect(outcomeTone('invented_outcome')).toBe('neutral');
	});
});

describe('formatWhen', () => {
	it('renders an absolute timestamp — the log is evidence, not a feed', () => {
		expect(formatWhen('2026-07-01T10:30:00Z')).toMatch(/^1 Jul 2026, \d{2}:\d{2}$/);
	});

	it('never throws on junk', () => {
		expect(formatWhen(null)).toBe('—');
		expect(formatWhen('not a date')).toBe('—');
	});
});
