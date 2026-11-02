import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	copyToClipboard,
	countdown,
	isExpired,
	remainingMs,
	REVEAL_TTL_MS,
	startReveal
} from './invitation';

describe('a revealed invitation link is ephemeral', () => {
	it('expires REVEAL_TTL_MS after it was revealed', () => {
		const t0 = 1_000_000;
		const reveal = startReveal('https://firm.test/set-password?token=rt_x', 'u1', t0);
		expect(remainingMs(reveal, t0)).toBe(REVEAL_TTL_MS);
		expect(isExpired(reveal, t0)).toBe(false);
		expect(isExpired(reveal, t0 + REVEAL_TTL_MS - 1)).toBe(false);
		// The whole point: the page must not keep holding a live set-password credential.
		expect(isExpired(reveal, t0 + REVEAL_TTL_MS)).toBe(true);
		expect(remainingMs(reveal, t0 + REVEAL_TTL_MS * 10)).toBe(0);
	});

	it('counts down in m:ss, rounding up so the last second is visible', () => {
		expect(countdown(60_000)).toBe('1:00');
		expect(countdown(45_400)).toBe('0:46');
		expect(countdown(1)).toBe('0:01');
		expect(countdown(0)).toBe('0:00');
	});
});

describe('copyToClipboard', () => {
	const original = globalThis.navigator;

	afterEach(() => {
		Object.defineProperty(globalThis, 'navigator', { value: original, configurable: true });
	});

	function stubNavigator(value: unknown): void {
		Object.defineProperty(globalThis, 'navigator', { value, configurable: true });
	}

	it('writes the text and reports success', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		stubNavigator({ clipboard: { writeText } });
		await expect(copyToClipboard('https://firm.test/set-password?token=rt_x')).resolves.toBe(true);
		expect(writeText).toHaveBeenCalledWith('https://firm.test/set-password?token=rt_x');
	});

	it('reports failure when the clipboard API is missing', async () => {
		// A non-secure context has no navigator.clipboard. The panel never prints the URL, so a copy
		// that silently did nothing would leave the operator with no link and no way to know —
		// hence a boolean the caller has to handle.
		stubNavigator({});
		await expect(copyToClipboard('x')).resolves.toBe(false);
	});

	it('reports failure when the browser refuses the write', async () => {
		stubNavigator({ clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
		await expect(copyToClipboard('x')).resolves.toBe(false);
	});
});
