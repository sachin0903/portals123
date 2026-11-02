import { describe, expect, it } from 'vitest';
import {
	devBypassAllowed,
	devPrincipal,
	requestHostname,
	type DevBypassRequest
} from './dev-bypass';

const token = (payload: unknown) =>
	`dev:${Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url')}`;

const local = (over: Partial<DevBypassRequest> = {}): DevBypassRequest => ({
	flag: '1',
	stage: 'dev',
	clientAddress: () => '127.0.0.1',
	headers: new Headers({ host: 'localhost:5173' }),
	...over
});

describe('devBypassAllowed — the four guards the API enforces', () => {
	it('allows a loopback, unproxied request on a development stage with the flag on', () => {
		expect(devBypassAllowed(local())).toEqual({ allowed: true, reason: '' });
		expect(devBypassAllowed(local({ stage: 'test', clientAddress: () => '::1' })).allowed).toBe(
			true
		);
		expect(devBypassAllowed(local({ headers: new Headers({ host: '[::1]:5173' }) })).allowed).toBe(
			true
		);
	});

	it('requires the explicit flag', () => {
		expect(devBypassAllowed(local({ flag: undefined })).allowed).toBe(false);
		expect(devBypassAllowed(local({ flag: 'true' })).allowed).toBe(false);
	});

	it('requires a stage from the allowlist, not merely "not production"', () => {
		// The portals used to accept anything but the literal string 'production'.
		for (const stage of ['staging', 'prod', 'Dev', 'development', undefined]) {
			expect(devBypassAllowed(local({ stage })).allowed).toBe(false);
		}
	});

	it('requires a loopback peer, and treats an unavailable address as not loopback', () => {
		expect(devBypassAllowed(local({ clientAddress: () => '10.0.0.4' })).allowed).toBe(false);
		expect(
			devBypassAllowed(
				local({
					clientAddress: () => {
						throw new Error('no address');
					}
				})
			).allowed
		).toBe(false);
	});

	it('refuses anything that looks proxied', () => {
		for (const h of ['x-forwarded-for', 'x-forwarded-host', 'x-real-ip', 'forwarded']) {
			const headers = new Headers({ host: 'localhost:5173', [h]: 'x' });
			expect(devBypassAllowed(local({ headers })).allowed).toBe(false);
		}
		expect(
			devBypassAllowed(local({ headers: new Headers({ host: 'portal.example.com' }) })).allowed
		).toBe(false);
	});
});

describe('devPrincipal', () => {
	it('parses a well-formed token when the bypass is allowed', () => {
		expect(devPrincipal(token({ sub: 'user_1', role: 'client' }), local())).toEqual({
			sub: 'user_1',
			role: 'client'
		});
	});

	it('returns null when any guard fails, whatever the token says', () => {
		expect(devPrincipal(token({ sub: 'user_1' }), local({ stage: 'staging' }))).toBeNull();
		expect(
			devPrincipal(token({ sub: 'user_1' }), local({ clientAddress: () => '10.1.1.1' }))
		).toBeNull();
	});

	it('returns null for anything that is not a dev token with a string sub', () => {
		expect(devPrincipal(undefined, local())).toBeNull();
		expect(devPrincipal('wos_sealed', local())).toBeNull();
		expect(devPrincipal('dev:not-base64-json!!', local())).toBeNull();
		expect(devPrincipal(token({ email: 'no-sub@x' }), local())).toBeNull();
		expect(devPrincipal(token('a string'), local())).toBeNull();
		expect(devPrincipal(token({ sub: 42 }), local())).toBeNull();
	});
});

describe('requestHostname', () => {
	it('strips the port and lowercases, and handles IPv6 literals', () => {
		expect(requestHostname('Localhost:5173')).toBe('localhost');
		expect(requestHostname('[::1]:5173')).toBe('::1');
		expect(requestHostname('portal.example.com')).toBe('portal.example.com');
		expect(requestHostname(null)).toBe('');
	});
});
