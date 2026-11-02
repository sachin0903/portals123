/**
 * The message to show for a failed mapping call. The BFF routes pass the API's own text through
 * (`error(status, detail)`), and that text is the useful part: a 409 says WHICH accounts are still
 * mapped, a 400 says the plan is not the client's. A bare status code tells the operator nothing.
 */
export async function responseMessage(res: Response, fallback: string): Promise<string> {
	try {
		const body = (await res.json()) as { message?: unknown };
		if (typeof body?.message === 'string' && body.message.trim()) return body.message;
	} catch {
		/* not JSON: fall through */
	}
	return `${fallback} (${res.status}).`;
}
