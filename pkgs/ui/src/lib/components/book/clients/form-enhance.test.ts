import { describe, expect, it, vi } from 'vitest';
import { withToast } from './form-enhance';

vi.mock('svelte-sonner', () => ({
	toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() }
}));

describe('withToast', () => {
	it('runs onSuccess after the data refresh, so a register that hid during edit reopens on fresh values', async () => {
		const order: string[] = [];
		const submit = withToast('saved', {
			onSuccess: () => {
				order.push('onSuccess');
			}
		});
		const callback = await submit({ formData: new FormData(), cancel: () => {} });
		expect(callback).toEqual(expect.any(Function));
		await callback!({
			result: { type: 'success', data: {} },
			update: async () => {
				order.push('update');
			}
		});
		expect(order).toEqual(['update', 'onSuccess']);
	});

	it('does not run onSuccess when the action failed', async () => {
		const onSuccess = vi.fn();
		const submit = withToast('saved', { onSuccess });
		const callback = await submit({ formData: new FormData(), cancel: () => {} });
		await callback!({
			result: { type: 'failure', data: { error: 'nope' } },
			update: async () => undefined
		});
		expect(onSuccess).not.toHaveBeenCalled();
	});
});
