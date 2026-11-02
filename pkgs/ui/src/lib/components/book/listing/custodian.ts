import { humanise } from './filters.js';

/**
 * The words a custodian code is printed as. The value on the wire stays the provider code
 * (`seccl`, `sei`) — that is what the listing filter matches. `humanise` would turn SEI into
 * "Sei", which is not the name.
 */
const CUSTODIAN_NAMES: Record<string, string> = {
	seccl: 'Seccl',
	sei: 'SEI'
};

export function custodianLabel(code: string): string {
	return CUSTODIAN_NAMES[code] ?? humanise(code);
}
