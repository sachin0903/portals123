import { describe, expect, it } from 'vitest';
import { isNavActive } from './nav-active.js';

describe('isNavActive', () => {
	it('matches an exact path', () => {
		expect(isNavActive('/accounts', '/accounts')).toBe(true);
	});

	it('matches a nested route under the tab', () => {
		expect(isNavActive('/documents', '/documents/123')).toBe(true);
	});

	it('does not match an unrelated path', () => {
		expect(isNavActive('/accounts', '/holdings')).toBe(false);
	});

	it('does not treat every path as under root', () => {
		expect(isNavActive('/', '/accounts')).toBe(false);
		expect(isNavActive('/', '/')).toBe(true);
	});

	// The regression this module exists for: a nav item's href can carry its own query (the
	// client profile tab's `?view=client`), which `pathname` never does. Comparing the two
	// verbatim never matched, so that tab only ever looked "active" via the anchor's own
	// hover/click pseudo-state — never from app state.
	it('matches when the href carries a query string', () => {
		expect(isNavActive('/account?view=client', '/account')).toBe(true);
	});

	it('matches a nested route under an href with a query string', () => {
		expect(isNavActive('/account?view=client', '/account/notifications')).toBe(true);
	});

	it('matches when the href carries a hash', () => {
		expect(isNavActive('/account#details', '/account')).toBe(true);
	});

	it('does not match a different path that merely starts with the same prefix', () => {
		expect(isNavActive('/account', '/accounts')).toBe(false);
	});
});
