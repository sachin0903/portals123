/** The uploaded flow export as JSON, or null when it is not an object. The API checks it is a
 *  flow export (`flows/portability.py::read_bundle`); this only refuses what cannot be sent as
 *  one — a page of text, an array, a broken file. */
export function parseBundle(text: string): Record<string, unknown> | null {
	try {
		const parsed: unknown = JSON.parse(text);
		return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
			? (parsed as Record<string, unknown>)
			: null;
	} catch {
		return null;
	}
}

/** The file name a flow export downloads as: its code, then the format's extension. The code is
 *  already a slug (`^[a-z0-9_]+$` on the API), so it is safe in a header as it is. */
export function exportFilename(code: string): string {
	return `${code}.flow.json`;
}
