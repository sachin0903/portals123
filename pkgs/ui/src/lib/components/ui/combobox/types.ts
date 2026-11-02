export interface ComboboxOption {
	value: string;
	label: string;
	/** Secondary line — an id, code or parent name. Also matched when searching. */
	hint?: string;
	disabled?: boolean;
}
