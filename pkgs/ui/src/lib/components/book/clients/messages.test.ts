import { describe, expect, it } from 'vitest';
import { setEnabledMessage } from './messages';

describe('setEnabledMessage', () => {
	it('follows the action result, not the state captured when the form was mounted', () => {
		// `use:enhance` evaluates its argument once: a message read from `contact.enabled` still
		// said "Client deactivated" after a reactivate.
		expect(setEnabledMessage({ enabled: true })).toBe('Client reactivated');
		expect(setEnabledMessage({ enabled: false })).toBe('Client deactivated');
	});
});
