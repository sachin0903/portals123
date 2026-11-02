import { isPensionProduct } from '@graphene/ui';

export type PensionShape = {
	product_type: string | null;
	is_pension?: boolean;
	custodian?: string | null;
	scheme_administrator?: string | null;
};

/** The shelf's answer, with the wrapper set as the fallback — the rule the client record gives:
 *  `is_pension` is a column a firm can see and correct, where the wrapper set has nine copies that
 *  do not agree. A mount over an older API that sends no column still works. */
export const isPension = (a: PensionShape): boolean =>
	a.is_pension ?? isPensionProduct(a.product_type);

/** An SEI SIPP whose scheme Morgan Lloyd administers. A Seccl SIPP is administered by Seccl,
 *  including when a product mapping still names Morgan Lloyd.
 *
 *  Named rather than inlined because it is asked twice and the two must not drift: an account that
 *  can be pushed is exactly an account that can later be drawn down, and a gate that said
 *  otherwise would offer a Drawdown button for a scheme nothing had created. */
export const isMorganLloydPension = (a: PensionShape): boolean =>
	isPension(a) && a.custodian === 'sei' && a.scheme_administrator === 'morgan-lloyd';
