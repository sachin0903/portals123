import { describe, expect, it } from 'vitest';
import { tasks } from './demo.js';

/** Local-calendar ISO date, mirroring how demo.ts stamps its due dates. */
function isoToday(): string {
	const d = new Date();
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

describe('demo tasks', () => {
	it('due dates are bare ISO dates', () => {
		for (const t of tasks) expect(t.due_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
	});

	it('due dates sit in the future, so the studio never opens on a red Overdue', () => {
		// The old fixed literals ('2026-08-04') aged into a permanent 'Overdue' as the studio's
		// first impression; the dates are now computed relative to module load. Bare ISO dates
		// compare correctly as strings.
		for (const t of tasks) expect(t.due_date! > isoToday()).toBe(true);
	});
});
