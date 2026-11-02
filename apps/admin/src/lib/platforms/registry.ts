/**
 * The provider registry — served by the API, held in one place here.
 *
 * IT USED TO BE SEVEN PLACES, none of them derived from the API: `LIVE_PROVIDERS` and `LIVE_META`
 * in `live.ts`, three fixture `meta` blocks under `lib/platforms/data/`, `PROVIDER_DEFS` on the
 * tenant enablement page, `PROVIDER_NAMES` in the tenant readiness tile, `PLATFORM_NAMES` in the
 * root layout and a `PROVIDERS` tuple in the tenant loader. All hard-coded to the same three
 * slugs, and already drifted — Seccl was described three different ways depending on which page
 * you were looking at, and adding a fourth custodian meant finding all seven.
 *
 * The real registry is `apps/api/app/providers.py::PROVIDERS` + `PROVIDER_META`, which
 * `sync_platforms()` also seeds `platform.platforms` from. It now travels on every platform
 * payload as `meta`, so this module holds shapes and formatting — not facts.
 */
import type { ConnectionStatus, PlatformRole, Transport } from './types';

/** How a provider presents. Mirrors `providers.PROVIDER_META`, which is the source of truth. */
export type ProviderMeta = {
	name: string;
	role: PlatformRole;
	description: string;
	transports: Transport[];
	/** What a "source" means for this provider — the caption on the enablement picker. */
	source_label: string;
	/** [label, warehouse relation] pairs — the entity counts worth showing for this provider. */
	kpis: [string, string][];
};

/** One row of `GET /admin/platforms`. */
export type PlatformStatus = {
	provider: string;
	meta: ProviderMeta;
	service: 'up' | 'down' | 'unconfigured' | 'starting';
	enabled_firms: number;
};

const STATUS: Record<string, ConnectionStatus> = {
	up: 'connected',
	down: 'error',
	unconfigured: 'paused',
	starting: 'degraded'
};

export const connectionStatus = (service: string): ConnectionStatus => STATUS[service] ?? 'error';

/**
 * What the card says beneath the badge. Every clause is a fact this deployment can state.
 *
 * `unconfigured` is spelled out rather than left as a neutral "paused": an unset
 * `INTERLACE_{PROVIDER}_URL` means every sync, refresh and rebuild for that custodian answers 503
 * while its pages still render — and both SEI and Morgan Lloyd shipped in exactly that state.
 */
export function connectionDetail(status: PlatformStatus): string {
	const firms = `${status.enabled_firms} enabled firm${status.enabled_firms === 1 ? '' : 's'}`;
	if (status.service === 'up') return `Data service available · ${firms}`;
	if (status.service === 'down') return `Data service unavailable · ${firms}`;
	if (status.service === 'starting') return `Data service starting · ${firms}`;
	return `Data service setup required · ${firms}`;
}

/** The provider a URL segment names, from the registry the API served.
 *
 *  IDENTITY since 0138 — the code IS the segment — kept as a lookup only so an unknown one still
 *  answers `undefined` rather than being passed through as if it were a provider. */
export const providerFor = (statuses: PlatformStatus[], segment: string): string | undefined =>
	statuses.find((s) => s.provider === segment)?.provider;

/** What `POST …/refresh` answered, from either door. */
export type RefreshResult = {
	/** Models the daemon's selector matched. Zero means nothing ran. */
	selected?: number;
	/**
	 * Set when the run selected no models. The daemon recompiles when a firm is enabled, so
	 * this is no longer the normal result of attaching a platform and refreshing.
	 */
	restart_required?: boolean;
};

/**
 * One explanation of a refresh, for both places that trigger one.
 *
 * There are two doors — the platform page's per-firm button and the firm's own Platforms card —
 * and they call the same endpoint. They had two hand-written descriptions of the same outcome, in
 * different words, which is how the same event comes to read as two different events.
 */
export function refreshMessage(result: RefreshResult): {
	tone: 'default' | 'destructive';
	text: string;
} {
	if (result.restart_required) {
		return {
			tone: 'destructive',
			text: 'No data was refreshed. Restart the provider service, then try again.'
		};
	}
	const n = result.selected ?? 0;
	return {
		tone: 'default',
		text: `Refresh queued for ${n} data set${n === 1 ? '' : 's'}. Check Runs for progress.`
	};
}
