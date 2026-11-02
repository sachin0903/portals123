import type { ProviderOption } from './types';

/** Account-enrolment templates that belong to this tenant's enabled SEI firm.

The warehouse lists every firm the sync has seen. The products sheet must not: the firm is
already on `platform.tenant_platforms.config.firm_id`, and picking another one would attach a
template that `POST /accountservice/accounts` will refuse. Templates with no parent are not
this firm's either — they are rows the model could not attribute.
*/
export function enrollmentTemplatesForFirm(
	templates: readonly ProviderOption[],
	firmId: string | null
): ProviderOption[] {
	if (!firmId) return [];
	return templates.filter((row) => row.parent === firmId);
}
