import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearClassificationCache, fetchMyClassification } from './classification';

beforeEach(() => clearClassificationCache());

describe('fetchMyClassification', () => {
	it('returns the public view and never asks without a token', async () => {
		const fetch = vi.fn();
		expect(await fetchMyClassification(null, { apiUrl: 'http://api.test', fetch })).toBeNull();
		expect(fetch).not.toHaveBeenCalled();

		fetch.mockResolvedValue({
			ok: true,
			json: async () => ({
				classification: {
					uid: 'u',
					name: 'D2C',
					slug: 'd2c',
					capabilities: { money_movement: true }
				}
			})
		});
		const row = await fetchMyClassification('tok', { apiUrl: 'http://api.test', fetch });
		expect(row?.slug).toBe('d2c');
		expect(fetch).toHaveBeenCalledWith('http://api.test/crm/classifications/mine', {
			headers: { Authorization: 'Bearer tok' }
		});
	});

	it('fails open to null so branding can fall back to the default design', async () => {
		const fetch = vi.fn(async () => ({ ok: false, status: 500 }));
		expect(
			await fetchMyClassification('tok', {
				apiUrl: 'http://api.test',
				fetch: fetch as unknown as typeof globalThis.fetch
			})
		).toBeNull();
	});

	it('caches per token for a short window, sweeping expired entries, and never caches a failure', async () => {
		let t = 1_000;
		const now = () => t;
		const ok = vi.fn(async () => ({
			ok: true,
			json: async () => ({ classification: { uid: 'u', name: 'A', slug: 'a', capabilities: {} } })
		})) as unknown as typeof globalThis.fetch;
		const opts = { apiUrl: 'http://api.test', fetch: ok, now };
		await fetchMyClassification('tok', opts);
		await fetchMyClassification('tok', opts);
		expect(ok).toHaveBeenCalledTimes(1);
		await fetchMyClassification('other', opts);
		expect(ok).toHaveBeenCalledTimes(2);
		// A second linked contact is a different book, so it is not served from the first's cache.
		await fetchMyClassification('tok', { ...opts, contactId: 92 });
		expect(ok).toHaveBeenCalledTimes(3);
		expect(ok).toHaveBeenLastCalledWith(
			'http://api.test/crm/classifications/mine?contact_id=92',
			expect.anything()
		);
		await fetchMyClassification('tok', { ...opts, contactId: 92 });
		expect(ok).toHaveBeenCalledTimes(3);
		t += 10_001;
		// Every entry has expired; the next write sweeps them and re-asks.
		await fetchMyClassification('tok', opts);
		expect(ok).toHaveBeenCalledTimes(4);

		const down = vi.fn(async () => ({
			ok: false,
			status: 503
		})) as unknown as typeof globalThis.fetch;
		await fetchMyClassification('third', { ...opts, fetch: down });
		await fetchMyClassification('third', { ...opts, fetch: down });
		expect(down).toHaveBeenCalledTimes(2);
	});
});
