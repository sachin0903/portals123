import { describe, it, expect } from 'vitest';
import {
	byUrgency,
	dueLabel,
	isOpen,
	isOverdue,
	SOURCE_KIND_LABELS,
	todayIso,
	type TaskRow
} from './tasks.js';

function task(over: Partial<TaskRow> = {}): TaskRow {
	return {
		id: 1,
		uid: 'tsk_1',
		subject_type: null,
		subject_id: null,
		source_kind: 'manual',
		source_ref: null,
		assignee_kind: 'user',
		assignee_user_id: 'u1',
		assignee_role_id: null,
		assignee_contact_id: null,
		assignee_name: 'Ada',
		category: 'review',
		title: 'Check the ID',
		description: null,
		due_date: null,
		priority: 'normal',
		status: 'open',
		created_at: '2026-07-01T09:00:00Z',
		completed_at: null,
		...over
	};
}

describe('SOURCE_KIND_LABELS', () => {
	it('names every closed source kind explicitly', () => {
		expect(SOURCE_KIND_LABELS).toEqual({
			manual: 'Manual',
			flow: 'Journey',
			document: 'Document',
			system: 'System'
		});
	});
});

describe('todayIso', () => {
	it('is the UTC calendar day, so every reader agrees with the API', () => {
		// The regression: overdue-ness was computed from a bare date parsed as LOCAL time against
		// `new Date()`, so the same task read overdue in Sydney and not in London — and neither
		// matched the API's own CURRENT_DATE scan.
		expect(todayIso(new Date('2026-07-28T23:30:00Z'))).toBe('2026-07-28');
		expect(todayIso(new Date('2026-07-29T00:30:00Z'))).toBe('2026-07-29');
	});
});

describe('isOverdue', () => {
	it('is false on the due day itself and true the day after', () => {
		const t = task({ due_date: '2026-07-28' });
		expect(isOverdue(t, '2026-07-28')).toBe(false);
		expect(isOverdue(t, '2026-07-29')).toBe(true);
		expect(isOverdue(t, '2026-07-27')).toBe(false);
	});

	it('does not depend on the machine clock once today is supplied', () => {
		const t = task({ due_date: '2026-07-01' });
		expect(isOverdue(t, '2026-07-28')).toBe(true);
	});

	it('ignores tasks with no due date, and closed ones', () => {
		expect(isOverdue(task({ due_date: null }), '2026-07-28')).toBe(false);
		expect(isOverdue(task({ due_date: '2026-01-01', status: 'done' }), '2026-07-28')).toBe(false);
		expect(isOverdue(task({ due_date: '2026-01-01', status: 'cancelled' }), '2026-07-28')).toBe(
			false
		);
	});
});

describe('dueLabel', () => {
	it('phrases the whole-day gap', () => {
		expect(dueLabel(task({ due_date: '2026-07-28' }), '2026-07-28')).toBe('Due today');
		expect(dueLabel(task({ due_date: '2026-07-29' }), '2026-07-28')).toBe('Due tomorrow');
		expect(dueLabel(task({ due_date: '2026-07-31' }), '2026-07-28')).toBe('Due in 3 days');
		expect(dueLabel(task({ due_date: '2026-07-27' }), '2026-07-28')).toBe('1 day overdue');
		expect(dueLabel(task({ due_date: '2026-07-24' }), '2026-07-28')).toBe('4 days overdue');
		expect(dueLabel(task({ due_date: null }), '2026-07-28')).toBe('');
	});

	it('does not slip a day across a DST boundary', () => {
		// 2026-03-29 is the UK clock change; date-only arithmetic must not round to 0 or 2 days.
		expect(dueLabel(task({ due_date: '2026-03-30' }), '2026-03-29')).toBe('Due tomorrow');
		expect(dueLabel(task({ due_date: '2026-10-26' }), '2026-10-25')).toBe('Due tomorrow');
	});
});

describe('byUrgency', () => {
	it('puts overdue work first, then the nearest due date, then the newest', () => {
		const overdue = task({ id: 1, due_date: '2026-07-01' });
		const soon = task({ id: 2, due_date: '2026-07-29' });
		const later = task({ id: 3, due_date: '2026-08-30' });
		const undated = task({ id: 4, due_date: null });
		const newer = task({ id: 9, due_date: null });
		const sorted = [later, undated, soon, newer, overdue].toSorted((a, b) =>
			byUrgency(a, b, '2026-07-28')
		);
		expect(sorted.map((t) => t.id)).toEqual([1, 2, 3, 9, 4]);
	});
});

describe('isOpen', () => {
	it('counts open and in_progress only', () => {
		expect(isOpen(task({ status: 'open' }))).toBe(true);
		expect(isOpen(task({ status: 'in_progress' }))).toBe(true);
		expect(isOpen(task({ status: 'done' }))).toBe(false);
	});
});
