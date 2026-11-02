/** Relief-at-source: a personal contribution is grossed up; an employer one is already gross. */
import { expect, it } from 'vitest';
import { grossOf, reliefOf } from './ras';
import type { Contribution } from './types';

/** Only the fields `ras.ts` reads. */
function contribution(over: Partial<Contribution> = {}): Contribution {
	return {
		net: 20_000,
		type: 'employer',
		...over
	} as Contribution;
}

it('grosses a personal contribution up by basic rate and leaves an employer one alone', () => {
	expect(grossOf(contribution({ type: 'personal', net: 800 }))).toBe(1_000);
	expect(grossOf(contribution({ type: 'employer', net: 1_000 }))).toBe(1_000);
	expect(reliefOf(contribution({ type: 'personal', net: 800 }))).toBe(200);
	expect(reliefOf(contribution({ type: 'employer', net: 1_000 }))).toBe(0);
});

it('grosses a third-party payment the same way as a personal one', () => {
	expect(grossOf(contribution({ type: 'personal', source: 'Third party', net: 800 }))).toBe(1_000);
	expect(reliefOf(contribution({ type: 'personal', source: 'Third party', net: 800 }))).toBe(200);
});
