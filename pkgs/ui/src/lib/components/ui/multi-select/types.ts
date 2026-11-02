export interface MultiSelectOption {
	value: string;
	label: string;
	/** Secondary line — an id, code or parent name. Also matched when searching. */
	hint?: string;
	/** Section heading this option sits under. Options are rendered in the order given, so a caller
	 *  sorts by group once and the sections come out stable; entries with no group render first,
	 *  under no heading (a "common"/"recent" block at the top). */
	group?: string;
	disabled?: boolean;
	/** Rows carrying this value, counted with this filter lifted (not the currently-visible set). */
	count?: number;
}
