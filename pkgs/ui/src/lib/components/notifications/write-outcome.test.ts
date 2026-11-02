import { describe, expect, it, vi } from 'vitest';
import { settleWrite } from './write-outcome';

describe('settleWrite', () => {
	it('leaves a fire-and-forget callback alone', async () => {
		const resync = vi.fn();
		await settleWrite(undefined, resync);
		await settleWrite(Promise.resolve(undefined), resync);
		expect(resync).not.toHaveBeenCalled();
	});

	it('leaves a write that landed alone', async () => {
		const resync = vi.fn();
		await settleWrite(true, resync);
		await settleWrite(Promise.resolve(true), resync);
		expect(resync).not.toHaveBeenCalled();
	});

	it('re-asks the server when the write was refused or lost', async () => {
		const resync = vi.fn();
		await settleWrite(false, resync);
		await settleWrite(Promise.resolve(false), resync);
		await settleWrite(Promise.reject(new Error('offline')), resync);
		expect(resync).toHaveBeenCalledTimes(3);
	});

	it('never rejects, whatever the callback did', async () => {
		await expect(settleWrite(Promise.reject(new Error('x')), () => {})).resolves.toBeUndefined();
	});
});
