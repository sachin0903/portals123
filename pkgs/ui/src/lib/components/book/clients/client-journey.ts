/** One client's journeys, as the record's Journeys section reads them. */

export type ClientJourneySummary = {
	uid: string;
	status: string;
	flow_name: string;
	flow_code: string;
	created_at: string;
	steps_done: number;
	steps_total: number;
};

/** Finished journeys stay on the Journeys section. The overview banner is the ones still open. */
const SETTLED = new Set(['completed', 'cancelled', 'failed']);

export function activeJourneys(journeys: readonly ClientJourneySummary[]): ClientJourneySummary[] {
	return journeys.filter((journey) => !SETTLED.has(journey.status));
}

export type ClientJourneyField = {
	key: string;
	label: string;
	section: string | null;
	field_type: string;
	options: unknown;
};

export type ClientJourneyForm = {
	step_key: string;
	title: string;
	status: string;
	submitted_at: string | null;
	submitted_by: string | null;
	answers: Record<string, unknown>;
	outputs: Record<string, unknown>;
	fields: ClientJourneyField[];
};

export type ClientJourneyDocument = {
	title: string;
	status: string;
	uid: string | null;
	name: string | null;
};

export type ClientJourneyOther = { uid: string; name: string };

export type ClientJourneyDetail = {
	uid: string;
	status: string;
	flow_name: string;
	flow_code: string;
	created_at: string;
	forms: ClientJourneyForm[];
	documents: ClientJourneyDocument[];
	others: ClientJourneyOther[];
};
