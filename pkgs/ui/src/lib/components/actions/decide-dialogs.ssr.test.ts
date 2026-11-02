/**
 * Reject · Approve with their words: a rejection asks why, approving says what it does, and both
 * post the uid of the run that holds the gate.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import DecideDialogs from './decide-dialogs.svelte';
import type { Enhance } from '../book/clients/form-enhance.js';

const enhance = (() => {}) as Enhance;

describe('DecideDialogs', () => {
	it('posts both decisions for the held run, with a note field on the rejection', () => {
		const { body } = render(DecideDialogs, {
			props: {
				runUid: 'child_1',
				isReview: true,
				action: '?/decide',
				enhance,
				approve: { title: 'Approve and send £25,000.00 now?', description: 'It goes to SEI.' },
				reject: { title: 'Reject this request?', description: 'Say why.' }
			}
		});
		expect(body).toContain('action="?/decide"');
		expect(body.match(/name="run_uid" value="child_1"/g)).toHaveLength(2);
		expect(body).toContain('name="decision" value="approve"');
		expect(body).toContain('name="decision" value="reject"');
		expect(body).toContain('name="is_review" value="true"');
		expect(body).toContain('name="note"');
		expect(body).toContain('>Approve<');
		expect(body).toContain('>Reject<');
	});
});
