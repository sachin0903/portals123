import { describe, expect, it } from 'vitest';
import { stepFormIdentity } from './form-identity';

describe('stepFormIdentity', () => {
	it('keeps two applicants’ copies of the same form type apart', () => {
		// Joint onboarding: two profile cases, both `core` / `applicant_core`. The page used to
		// key `form_uid:step_key`, which is identical here, so Prev/Next leaked answers.
		expect(stepFormIdentity('case-alex', 'core', null)).not.toBe(
			stepFormIdentity('case-sam', 'core', null)
		);
	});

	it('keeps two fanned instances inside one case apart', () => {
		expect(stepFormIdentity('case-joint', 'pension', '11')).not.toBe(
			stepFormIdentity('case-joint', 'pension', '22')
		);
	});

	it('treats a missing participant as the same instance as an empty one', () => {
		expect(stepFormIdentity('c1', 'core', null)).toBe(stepFormIdentity('c1', 'core', ''));
		expect(stepFormIdentity('c1', 'core', undefined)).toBe(stepFormIdentity('c1', 'core', null));
	});
});
