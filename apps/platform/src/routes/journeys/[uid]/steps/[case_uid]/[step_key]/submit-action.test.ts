/**
 * What the submit action does with the API's answer.
 *
 * THE DEFECT THIS EXISTS FOR. Fulfilling a step and the step's WRITE-BACK succeeding are two
 * different things: the API stores the answers, then the handler tries to put them in the book, and
 * a refusal there (a fee package the account cannot take) blocks the step while the request itself
 * answers 200. This action read `res.ok` alone, so the adviser was told the form had saved and
 * moved on to the next one, leaving a blocked journey behind them — with the answers recorded, so
 * the form they came from then read as submitted. The refusal now stops them on the form that
 * caused it.
 */
import { describe, expect, it } from 'vitest';
import { actions } from './+page.server';

const locals = { permissions: ['cases:write'], role: 'adviser', accessToken: 'tok' };
const params = { uid: 'j1', case_uid: 'c1', step_key: 'preferences' };

function request(): Request {
	const body = new FormData();
	body.set('answers', JSON.stringify({ account_name: 'Growth SIPP' }));
	return new Request('http://localhost/journeys/j1/steps/c1/preferences?/submit', {
		method: 'POST',
		body
	});
}

/** Answers the submit POST with `answer`; every other call is the execution re-read. */
function fetchStub(answer: Record<string, unknown>): typeof fetch {
	return (async (input: RequestInfo | URL) =>
		new Response(JSON.stringify(String(input).includes('/form') ? answer : { cases: [] }), {
			status: 200,
			headers: { 'content-type': 'application/json' }
		})) as unknown as typeof fetch;
}

async function submit(answer: Record<string, unknown>): Promise<unknown> {
	try {
		return await actions.submit({
			params,
			request: request(),
			locals,
			fetch: fetchStub(answer)
		} as never);
	} catch (thrown) {
		return thrown; // a redirect is thrown, not returned
	}
}

describe('the step form submit action', () => {
	it('keeps the adviser on the form when the write-back was refused', async () => {
		const result = (await submit({
			ok: false,
			blocked: true,
			reason: "That fee package isn't available for this account."
		})) as { status: number; data: { error: string } };

		expect(result.status).toBe(422);
		expect(result.data.error).toBe("That fee package isn't available for this account.");
	});

	it('says something useful even if the refusal carries no reason', async () => {
		const result = (await submit({ ok: false, blocked: true })) as { data: { error: string } };
		expect(result.data.error).toMatch(/could not be saved/i);
	});

	it('moves on when the step really did complete', async () => {
		const result = (await submit({ ok: true, blocked: false })) as { status: number };
		expect(result.status).toBe(303);
	});
});
