import { describe, expect, it } from 'vitest';
import {
	deviceLabel,
	fillCurrentBrowser,
	readSignInClient,
	signInClient,
	workosClientFields
} from './client-context';

const CHROME_MAC =
	'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const EDGE_WIN =
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0';
const SAFARI_IOS =
	'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';
const FIREFOX_LINUX = 'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0';
const CHROME_ANDROID =
	'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';

describe('deviceLabel', () => {
	it('names the browser and the operating system', () => {
		expect(deviceLabel(CHROME_MAC)).toBe('Chrome on macOS');
		expect(deviceLabel(EDGE_WIN)).toBe('Edge on Windows');
		expect(deviceLabel(SAFARI_IOS)).toBe('Safari on iOS');
		expect(deviceLabel(FIREFOX_LINUX)).toBe('Firefox on Linux');
		expect(deviceLabel(CHROME_ANDROID)).toBe('Chrome on Android');
	});

	it('returns null when there is nothing to read', () => {
		expect(deviceLabel(null)).toBeNull();
		expect(deviceLabel('')).toBeNull();
		expect(deviceLabel('   ')).toBeNull();
	});
});

describe('signInClient', () => {
	it('drops blank values and control characters, and caps a huge user agent', () => {
		expect(signInClient({ ipAddress: '  ', userAgent: null })).toEqual({});
		expect(signInClient({ ipAddress: '203.0.113.8\n', userAgent: 'Mozilla\u0000/5.0' })).toEqual({
			ipAddress: '203.0.113.8',
			userAgent: 'Mozilla/5.0'
		});
		const huge = 'A'.repeat(400);
		expect(signInClient({ userAgent: huge }).userAgent).toHaveLength(256);
	});
});

describe('readSignInClient', () => {
	it('reads the address and the user-agent header', () => {
		const request = {
			headers: { get: (name: string) => (name === 'user-agent' ? CHROME_MAC : null) }
		};
		expect(readSignInClient(request, () => '203.0.113.8')).toEqual({
			ipAddress: '203.0.113.8',
			userAgent: CHROME_MAC
		});
	});

	it('still returns the user agent when the address cannot be resolved', () => {
		const request = { headers: { get: () => 'Firefox/121.0' } };
		expect(
			readSignInClient(request, () => {
				throw new Error('no address');
			})
		).toEqual({ userAgent: 'Firefox/121.0' });
	});
});

describe('fillCurrentBrowser', () => {
	const blank = {
		current: true,
		ip_address: null,
		user_agent: null,
		device: null
	};

	it('names this browser from the request when WorkOS stored nothing', () => {
		expect(
			fillCurrentBrowser([blank], { ipAddress: '203.0.113.8', userAgent: CHROME_MAC })
		).toEqual([
			{
				current: true,
				ip_address: '203.0.113.8',
				user_agent: CHROME_MAC,
				device: 'Chrome on macOS'
			}
		]);
	});

	it('leaves another session blank and keeps a user agent WorkOS already stored', () => {
		const other = { current: false, ip_address: null, user_agent: null, device: null };
		const stored = {
			current: true,
			ip_address: '198.51.100.4',
			user_agent: 'Firefox/121.0',
			device: 'Firefox'
		};
		expect(
			fillCurrentBrowser([other, stored], { ipAddress: '203.0.113.8', userAgent: CHROME_MAC })
		).toEqual([other, stored]);
	});
});

describe('workosClientFields', () => {
	it('forwards only the fields we actually have', () => {
		expect(workosClientFields(undefined)).toEqual({});
		expect(workosClientFields({ ipAddress: '203.0.113.8' })).toEqual({ ipAddress: '203.0.113.8' });
	});
});
