/** One pickable record: the identifier that gets sent, and how a human recognises it. */
export type RecordOption = {
	/** What the form posts — a vendor GUID, a scheme reference. */
	value: string;
	/** What the person reads. Never blank: a source with no name falls back to the value. */
	label: string;
	/** A dimmer second line — an email, a status. What tells two "J Smith"s apart. */
	hint?: string;
};
