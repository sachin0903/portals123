import { describe, expect, it } from 'vitest';
import {
	RETURN_TO_PARAM,
	loginNotice,
	loginPath,
	returnToFrom,
	safeReturnTo,
	withReturnTo
} from './return-to';

describe('safeReturnTo', () => {
	it('accepts a same-origin path, keeping query and hash', () => {
		expect(safeReturnTo('/documents')).toBe('/documents');
		expect(safeReturnTo('/clients/abc?tab=notes#top')).toBe('/clients/abc?tab=notes#top');
	});

	it('normalises traversal instead of trusting it', () => {
		expect(safeReturnTo('/documents/../tasks')).toBe('/tasks');
		// Traversal above the root resolves to the root, not off-origin.
		expect(safeReturnTo('/../../etc/passwd')).toBe('/etc/passwd');
	});

	it('rejects anything that could leave the origin', () => {
		for (const bad of [
			'https://evil.test/steal',
			'http://evil.test',
			'//evil.test/steal',
			'/\\evil.test',
			'/\\/evil.test',
			'\\/evil.test',
			'javascript:alert(1)',
			'data:text/html,<script>',
			'documents', // no leading slash
			''
		]) {
			expect(safeReturnTo(bad), bad).toBeNull();
		}
	});

	it('rejects control characters (redirect splitting)', () => {
		expect(safeReturnTo('/documents\r\nSet-Cookie: a=b')).toBeNull();
		expect(safeReturnTo('/documents\u0000')).toBeNull();
	});

	it('rejects absent, non-string and over-long values', () => {
		expect(safeReturnTo(null)).toBeNull();
		expect(safeReturnTo(undefined)).toBeNull();
		expect(safeReturnTo(`/${'a'.repeat(600)}`)).toBeNull();
	});

	it('refuses to bounce back into the auth flow', () => {
		for (const bad of [
			'/login',
			'/login?next=/x',
			'/logout',
			'/verify',
			'/mfa',
			'/mfa/enroll',
			'/set-password?token=abc',
			'/forgot-password'
		]) {
			expect(safeReturnTo(bad), bad).toBeNull();
		}
		// A path that merely starts with the same letters is fine.
		expect(safeReturnTo('/verifications')).toBe('/verifications');
	});
});

describe('returnToFrom', () => {
	it('reads a valid next off the login URL', () => {
		const url = new URL(`https://portal.test/login?${RETURN_TO_PARAM}=%2Fdocuments%2F42`);
		expect(returnToFrom(url)).toBe('/documents/42');
	});

	it('falls back when next is missing or unsafe', () => {
		expect(returnToFrom(new URL('https://portal.test/login'))).toBe('/');
		const evil = new URL(`https://portal.test/login?${RETURN_TO_PARAM}=https%3A%2F%2Fevil.test`);
		expect(returnToFrom(evil)).toBe('/');
		expect(returnToFrom(evil, '/home')).toBe('/home');
	});
});

describe('loginPath', () => {
	it('preserves where the user was', () => {
		expect(loginPath({ pathname: '/documents/42', search: '?tab=activity' })).toBe(
			'/login?next=%2Fdocuments%2F42%3Ftab%3Dactivity'
		);
	});

	it('stays clean when there is nothing worth preserving', () => {
		expect(loginPath({ pathname: '/' })).toBe('/login');
		expect(loginPath({ pathname: '/login', search: '?next=%2Fx' })).toBe('/login');
	});

	it('round-trips through returnToFrom', () => {
		const path = loginPath({ pathname: '/tasks', search: '?status=open' });
		expect(returnToFrom(new URL(path, 'https://portal.test'))).toBe('/tasks?status=open');
	});
});

describe('withReturnTo', () => {
	it('hands the destination on to the next step of the flow', () => {
		expect(withReturnTo('/mfa', { pathname: '/documents/42' })).toBe('/mfa?next=%2Fdocuments%2F42');
		expect(withReturnTo('/mfa', { pathname: '/' })).toBe('/mfa');
		// An unsafe destination is dropped, never forwarded.
		expect(withReturnTo('/mfa', { pathname: '//evil.test' })).toBe('/mfa');
	});
});

describe('loginNotice', () => {
	// Two of the five sign-in outcomes used to exit through a bare '/login?check=1' or
	// '/login?expired=1', which threw the destination away: the user confirmed their email or
	// retried the timed-out code and then landed on the dashboard instead of the page they wanted.
	it('carries the notice AND the destination', () => {
		expect(loginNotice('check', '/documents/42?tab=activity')).toBe(
			'/login?check=1&next=%2Fdocuments%2F42%3Ftab%3Dactivity'
		);
		expect(loginNotice('expired', '/tasks')).toBe('/login?expired=1&next=%2Ftasks');
	});

	it('omits an empty or worthless destination', () => {
		expect(loginNotice('check')).toBe('/login?check=1');
		expect(loginNotice('check', null)).toBe('/login?check=1');
		expect(loginNotice('check', '/')).toBe('/login?check=1');
	});

	it('never forwards an unsafe destination (open redirect)', () => {
		for (const evil of ['https://evil.test/steal', '//evil.test', '/\\evil.test', '/login?x=1']) {
			expect(loginNotice('expired', evil)).toBe('/login?expired=1');
		}
	});

	it('round-trips through returnToFrom', () => {
		const path = loginNotice('expired', '/tasks?status=open');
		expect(returnToFrom(new URL(path, 'https://portal.test'))).toBe('/tasks?status=open');
	});
});
