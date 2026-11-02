/** What a custom template actually RECEIVES.
 *
 *  A notification rule's `email_code` is the only door a custom template has: the built-in send sites
 *  are keyed by code, and the flows `send_email` action resolves `documents.templates`, a different
 *  system. So `_template_context` in the notification consumer IS the vocabulary, and the parity block
 *  reads that Python source — brittle by construction, hence the non-vacuity check.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { NOTIFICATION_SUPPLIED } from './merge-fields';

describe('parity with the notification consumer’s own context', () => {
	// fileURLToPath, not `.pathname` — on Windows the latter yields `/C:/…`, which `join` mangles.
	const source = readFileSync(
		join(
			fileURLToPath(new URL('../../../../../../', import.meta.url)),
			'apps/api/app/notifications/email_consumer.py'
		),
		'utf8'
	);

	/** The literal keys of the `ctx` dict `_template_context` builds. */
	const pythonKeys = (): string[] => {
		const body = source.slice(
			source.indexOf('ctx: dict[str, Any] = {'),
			source.indexOf('\n    return ctx')
		);
		return [...body.matchAll(/^\s{8}"(\w+)":/gm)].map((m) => m[1]);
	};

	it('parses the Python source, so the assertions below cannot pass vacuously', () => {
		expect(source.length).toBeGreaterThan(0);
		expect(pythonKeys().length).toBeGreaterThan(10);
	});

	it('lists exactly what the consumer passes, less the nested brand scope', () => {
		// `brand` is a nested dict, so it is deliberately not in the flat list of insertable names.
		const expected = pythonKeys()
			.filter((k) => k !== 'brand')
			.sort();
		expect([...NOTIFICATION_SUPPLIED].sort()).toEqual(expected);
	});
});
