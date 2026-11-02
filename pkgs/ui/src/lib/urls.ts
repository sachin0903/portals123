/** An address on a third-party site a page may load or link to: http(s) only, so a
 *  `javascript:` value never becomes a link. */
export function isWebUrl(value: string | null | undefined): value is string {
	if (!value) return false;
	try {
		const { protocol } = new URL(value);
		return protocol === 'https:' || protocol === 'http:';
	} catch {
		return false;
	}
}
