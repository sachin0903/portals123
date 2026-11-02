import { WRAPPER_CODE } from '@graphene/ui';
import type { CatalogueTrigger, NotificationRule } from '@graphene/ui';

/** Which notification rules render each email template, keyed by template `code` → human labels.
 *  A rule's EFFECTIVE template is its own `email_code`, else its audience's catalogue default, else
 *  the general `notification` wrapper. Disabled rules are skipped. Feeds the delete confirm. */
export function buildTemplateUsage(
	rules: NotificationRule[],
	catalogue: CatalogueTrigger[]
): Record<string, string[]> {
	const byKey = new Map(catalogue.map((t) => [t.key, t]));
	const out: Record<string, string[]> = {};
	for (const r of rules) {
		if (!r.enabled) continue;
		const trig = byKey.get(r.trigger_key);
		const aud = trig?.audiences.find((a) => a.key === r.audience);
		const code = r.email_code ?? aud?.default_email_code ?? WRAPPER_CODE;
		const label = trig ? `${trig.label}${aud ? ` (${aud.label})` : ''}` : r.trigger_key;
		(out[code] ??= []).push(label);
	}
	return out;
}
