/** A Postcoder autocomplete suggestion. */
export interface AddressSuggestion {
	id: string;
	type: string;
	summaryline: string;
	locationsummary?: string;
	/** Number of addresses represented; `type`, not this count, decides whether it is a container. */
	count: number;
}

/** The raw address Postcoder returns from `retrieve`. */
export interface PostcoderAddress {
	summaryline?: string;
	addressline1?: string;
	addressline2?: string;
	addressline3?: string;
	organisation?: string;
	buildingname?: string;
	premise?: string;
	street?: string;
	dependentlocality?: string;
	posttown?: string;
	postcode?: string;
	county?: string;
	state?: string;
	stateabbreviation?: string;
	country?: string;
	countrycode?: string;
	latitude?: string | number;
	longitude?: string | number;
}

/** The shape a form stores. Flattened from Postcoder's many optional parts. */
export interface Address {
	line1: string;
	line2?: string;
	town: string;
	county?: string;
	postcode: string;
	country: string;
	countryCode: string;
	latitude?: number;
	longitude?: number;
}

/**
 * Postcoder splits an address across organisation / premise / buildingname / street, any of
 * which may be blank. Collapse them into the two lines a form actually holds.
 *
 * Ported verbatim from the React original so a given address normalises identically in both
 * products — the composition order is not arbitrary, it is what makes "1 Woodlands Hall,
 * Bakers Lane" come out in that order rather than reversed.
 */
export function normaliseAddress(raw: PostcoderAddress, fallbackCountryCode = ''): Address {
	const organisation = (raw.organisation ?? '').trim();
	const buildingname = (raw.buildingname ?? '').trim();
	const premise = (raw.premise ?? '').trim();
	const street = (raw.street ?? '').trim();
	const dependent = (raw.dependentlocality ?? '').trim();

	const numberAndBuilding = [premise, buildingname].filter(Boolean).join(' ');
	const streetLine = [numberAndBuilding, street].filter(Boolean).join(', ');
	// Postcoder's configured address lines preserve each country's native ordering. UK responses
	// also carry the component fields below; international responses must prefer addressline* or a
	// Japanese/French/US address is reconstructed in a UK order.
	const componentLine1 = organisation
		? streetLine
			? `${organisation}, ${streetLine}`
			: organisation
		: (streetLine ?? '') || (raw.summaryline ?? '');
	const line1 = (raw.addressline1 ?? '').trim() || componentLine1;
	const line2 =
		[raw.addressline2, raw.addressline3]
			.map((part) => (part ?? '').trim())
			.filter(Boolean)
			.join(', ') ||
		dependent ||
		undefined;

	const returnedCountryCode = (raw.countrycode ?? '').toUpperCase();
	const countryCode = /^[A-Z]{2}$/.test(returnedCountryCode)
		? returnedCountryCode
		: fallbackCountryCode.trim().toUpperCase();
	const latitude = raw.latitude === undefined ? NaN : Number(raw.latitude);
	const longitude = raw.longitude === undefined ? NaN : Number(raw.longitude);

	return {
		line1,
		line2,
		town: raw.posttown ?? '',
		county: raw.state || raw.stateabbreviation || raw.county || undefined,
		postcode: raw.postcode ?? '',
		country: raw.country ?? countryCode,
		countryCode,
		latitude: Number.isFinite(latitude) ? latitude : undefined,
		longitude: Number.isFinite(longitude) ? longitude : undefined
	};
}

/** Postcoder's `type`, not its count, decides whether a suggestion can be retrieved. */
export function isAddressSuggestionContainer(suggestion: AddressSuggestion): boolean {
	return suggestion.type !== 'ADD';
}

/** A one-line rendering, for showing what was chosen. */
export function formatAddress(address: Address): string {
	return [address.line1, address.line2, address.town, address.county, address.postcode]
		.filter(Boolean)
		.join(', ');
}
