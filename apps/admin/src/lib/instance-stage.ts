/**
 * Which deployment this portal is, from `APP_STAGE` — the same name the API uses
 * (`dev`, `staging`, `production`). An absent value is production: a portal that does not
 * know its stage must not offer a control that only exists off production.
 */
export function instanceStage(raw: string | null | undefined): string {
	const stage = (raw ?? '').trim().toLowerCase();
	return stage || 'production';
}

/** Outbound email is restricted on these two instances and on no others. */
export function showsOutboundEmailGuard(stage: string | null | undefined): boolean {
	const name = instanceStage(stage);
	return name === 'dev' || name === 'staging';
}
