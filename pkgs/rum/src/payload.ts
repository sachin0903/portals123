/** A few KB. Anything larger is dropped rather than sent. */
export const RUM_BODY_MAX = 4096;

export function beaconBody(payload: Record<string, unknown>): string | null {
	const body = JSON.stringify(payload);
	if (body.length > RUM_BODY_MAX) return null;
	return body;
}
