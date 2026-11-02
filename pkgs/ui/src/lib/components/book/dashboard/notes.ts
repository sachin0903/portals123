/**
 * A member of staff's own notes — the checklist under the dashboard rail.
 *
 * Stored centrally (`platform.staff_notes`, `GET/POST /me/notes`, `PATCH/DELETE /me/notes/{uid}`)
 * so a list follows its owner to the next machine; RLS admits the owner alone. The portal loads
 * the list with the dashboard and proxies the writes under the same paths, so `Scratchpad` talks
 * to a relative `endpoint` and never holds a token.
 */

/** A note as the API returns it. */
export type StaffNote = {
	uid: string;
	body: string;
	done: boolean;
	position: number;
};

/** The four calls `Scratchpad` makes. Injected so a test can stand in for the network; the
 *  default speaks to the portal's proxy routes under `endpoint`. */
export type NotesClient = {
	add: (body: string) => Promise<StaffNote>;
	edit: (uid: string, patch: { body?: string; done?: boolean }) => Promise<StaffNote>;
	remove: (uid: string) => Promise<void>;
};

export function notesClient(endpoint: string, fetchFn: typeof fetch = fetch): NotesClient {
	const call = async <T>(path: string, method: string, body?: object): Promise<T> => {
		const res = await fetchFn(path, {
			method,
			headers: body ? { 'content-type': 'application/json' } : undefined,
			body: body ? JSON.stringify(body) : undefined
		});
		if (!res.ok) throw new Error(`Notes: ${method} ${path} failed (${res.status})`);
		return (res.status === 204 ? undefined : await res.json()) as T;
	};
	return {
		add: (body) => call<StaffNote>(endpoint, 'POST', { body }),
		edit: (uid, patch) => call<StaffNote>(`${endpoint}/${encodeURIComponent(uid)}`, 'PATCH', patch),
		remove: (uid) => call<void>(`${endpoint}/${encodeURIComponent(uid)}`, 'DELETE')
	};
}
