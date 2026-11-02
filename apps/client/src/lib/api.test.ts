import { describe, expect, it } from 'vitest';
import { detail, publicId } from './api';

describe('publicId', () => {
	it('accepts the minted shape and the legacy prefixed rows, trimmed', () => {
		expect(publicId('V1StGXR8Z5jd')).toBe('V1StGXR8Z5jd');
		expect(publicId(' seccl_AXB1D16C3 ')).toBe('seccl_AXB1D16C3');
		expect(publicId('acc_b014d4b5d83145d9')).toBe('acc_b014d4b5d83145d9');
		expect(publicId('req-1')).toBe('req-1');
	});

	it('refuses anything that could leave its path segment', () => {
		// The reproduced case: `new URL(\`${api}/tasks/${uid}/complete\`)` with this value became
		// `${api}/messaging/thread/messages?/complete` — the caller's bearer, any route they name.
		expect(publicId('x/../../messaging/thread/messages?')).toBeNull();
		for (const bad of ['a/b', 'a?b=1', 'a#x', 'a%2Fb', 'a&b', 'a.b', 'a b', '', '   ']) {
			expect(publicId(bad)).toBeNull();
		}
		expect(publicId('a'.repeat(65))).toBeNull();
		expect(publicId(null)).toBeNull();
		expect(publicId(undefined)).toBeNull();
		expect(publicId(new File([], 'f'))).toBeNull();
	});
});

const response = (status: number, body: unknown) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

describe('detail', () => {
	it('keeps useful validation guidance', async () => {
		expect(await detail(response(422, { detail: 'Choose a PDF, Word document or image.' }))).toBe(
			'Choose a PDF, Word document or image.'
		);
	});

	it('removes validation framework prefixes', async () => {
		expect(
			await detail(response(422, { detail: [{ msg: 'Value error, Enter a valid date.' }] }))
		).toBe('Enter a valid date.');
	});

	it('does not expose backend details or statuses', async () => {
		expect(await detail(response(500, { detail: 'SQL exception on /documents/42' }))).toBe(
			'We couldn’t complete that request. Please try again.'
		);
		expect(await detail(response(422, { detail: 'Invalid API session' }))).toBe(
			'We couldn’t complete that request. Please try again.'
		);
	});
});
