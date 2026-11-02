// Relief at source for the admin workbench. The amounts are the canonical ones
// in `@graphene/ui`: personal and third-party payments are net, employer is gross.
import {
	grossOf as canonicalGross,
	reliefOf as canonicalRelief,
	type ContributionCategory
} from '@graphene/ui/book/contributions';
import type { Contribution } from './types';

type ReliefInput = Pick<Contribution, 'type' | 'net'> & Partial<Pick<Contribution, 'source'>>;

function categoryOf(c: ReliefInput): ContributionCategory {
	if (c.source === 'Third party') return 'third_party_contribution';
	if (c.type === 'employer') return 'employer_contribution';
	return 'personal_contribution';
}

/** Gross contribution. Personal and third-party nets are grossed up; employer is already gross. */
export function grossOf(c: ReliefInput): number {
	return canonicalGross({ amount: c.net, category: categoryOf(c) });
}

/** Basic-rate relief reclaimed. Zero for an employer contribution. */
export function reliefOf(c: ReliefInput): number {
	return canonicalRelief({ amount: c.net, category: categoryOf(c) });
}
