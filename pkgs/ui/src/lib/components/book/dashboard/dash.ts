/**
 * The firm dashboard's data — what `FirmDashboard` renders, loaded by each portal's own route.
 *
 * The platform loads it at `/` with the caller's session; admin's operate-as-firm view loads the
 * same four reads with `?tenant=` at `/tenants/{uid}/operate`. One shape, so the two dashboards
 * cannot drift in what they show.
 */
import type { AppExecutionRow } from '../journeys/applications.js';
import type { ReviewRow } from '../reviews/index.js';
import type { BookRow } from './book.js';
import type { StaffNote } from './notes.js';

/** `GET /crm/overview` — the fields the KPI tiles read. Every money figure states where it came
 *  from and when (`values_as_of`), rather than a fabricated performance series. */
export type FirmOverview = {
	clients: number;
	accounts: number;
	aum: number;
	advised: number;
	execution_only: number;
	clients_funded: number;
	/** Accounts that can hold value (not `draft`, not `closed`) — the AUM basis. */
	valued_accounts: number;
	/** When the account values were last written (the batch snapshot's `updated_at`). */
	values_as_of: string | null;
	/** The provider the values came from, or null when nothing is linked. */
	value_source: string | null;
};

/** `GET /crm/contacts` row as the dashboard's book reads it. */
export type DashClientRow = BookRow & { value_as_of?: string | null };

/**
 * The book and the queue — three of the dashboard's four reads, in one streamed object.
 *
 * The fourth, `GET /crm/overview`, is NOT in here: it is streamed on its own (`FirmDashboard`'s
 * `overview` prop) so the KPI tiles — the "£7.8m" that is the page's largest paint — land the
 * moment that one summary query answers, rather than waiting for the slowest of the four. Folding
 * it into this object held the hero back behind the paged, faceted book read and the two queue
 * reads, and the page's LCP moved from the first paint to the last.
 */
export type Dash = {
	clients: DashClientRow[];
	matchCount: number;
	facets: Record<string, string[]>;
	/** The ends of each range column — what the panel's sliders draw their track from. */
	bounds: Record<string, number[]>;
	clientsError: boolean;
	/** A 403 is "this login may not read the book" — permanent, not a retry. */
	clientsForbidden: boolean;
	/** The caller's own live onboarding journeys (`GET /applications?mine=1`). */
	journeys: AppExecutionRow[];
	journeysError: boolean;
	reviews: ReviewRow[];
	reviewsError: boolean;
	/** The reader's own notes (`GET /me/notes`), or null where there is no reader to own them —
	 *  admin's operate-as-firm view is an elevated read of the firm, not a person's dashboard. */
	notes: StaffNote[] | null;
	notesError: boolean;
};

/** Every place the dashboard links to, so it can mount under any prefix. */
export type DashLinks = {
	clients: string;
	accounts: string;
	reviews: string;
	journeys: string;
	client: (uid: string) => string;
	journey: (uid: string) => string;
	review: (uid: string) => string;
};

export const PLATFORM_DASH_LINKS: DashLinks = {
	clients: '/clients',
	accounts: '/accounts',
	reviews: '/reviews',
	journeys: '/journeys',
	client: (uid) => `/clients/${uid}`,
	journey: (uid) => `/journeys/${uid}`,
	review: (uid) => `/reviews?open=${uid}`
};

/** The same links under a path prefix — admin's `/tenants/{uid}/operate`. */
export function prefixedDashLinks(prefix: string): DashLinks {
	return {
		clients: `${prefix}/clients`,
		accounts: `${prefix}/accounts`,
		reviews: `${prefix}/reviews`,
		journeys: `${prefix}/journeys`,
		client: (uid) => `${prefix}/clients/${uid}`,
		journey: (uid) => `${prefix}/journeys/${uid}`,
		review: (uid) => `${prefix}/reviews?open=${uid}`
	};
}
