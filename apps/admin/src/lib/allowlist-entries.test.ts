import { describe, expect, it } from 'vitest';
import {
	allowlistPasteSummary,
	canonicalAllowlistEntry,
	mergeAllowlistPaste
} from './allowlist-entries';

describe('canonicalAllowlistEntry', () => {
	it('keeps an address and writes a bare domain with @', () => {
		expect(canonicalAllowlistEntry(' Ada@Firm.Test ')).toBe('ada@firm.test');
		expect(canonicalAllowlistEntry('@Grapheneplatforms.com')).toBe('@grapheneplatforms.com');
		expect(canonicalAllowlistEntry('grapheneplatforms.com')).toBe('@grapheneplatforms.com');
	});

	it('rejects a token that is not one address or one domain', () => {
		expect(canonicalAllowlistEntry('not an address')).toBeNull();
		expect(canonicalAllowlistEntry('a@b@c')).toBeNull();
		expect(canonicalAllowlistEntry('@')).toBeNull();
	});
});

describe('mergeAllowlistPaste', () => {
	it('splits commas, semicolons and new lines, and skips what is already listed', () => {
		const result = mergeAllowlistPaste(
			['@grapheneplatforms.com'],
			'ada@firm.test, gio@firm.test;\n@grapheneplatforms.com bob@firm.test'
		);
		expect(result.entries).toEqual([
			'@grapheneplatforms.com',
			'ada@firm.test',
			'gio@firm.test',
			'bob@firm.test'
		]);
		expect(result.added).toBe(3);
		expect(result.duplicate).toBe(1);
		expect(result.rejected).toBe(0);
	});

	it('counts a token that cannot be an address', () => {
		const result = mergeAllowlistPaste([], 'ada@firm.test, not_an_address');
		expect(result.entries).toEqual(['ada@firm.test']);
		expect(result.rejected).toBe(1);
		expect(allowlistPasteSummary(result)).toBe('Added 1. 1 could not be read.');
	});
});
