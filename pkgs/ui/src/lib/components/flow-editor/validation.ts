/**
 * Publish issues, grouped for the builder.
 *
 * The engine's `publish_issues` speaks to a human — "Branch “Advised or not” has no “otherwise”
 * branch" — and names a node by its TITLE, because that is what an author called it. Good copy,
 * useless for putting a badge on the right node: two nodes may share a title and an untitled node
 * falls back to its kind. So the validate endpoint carries the node id alongside the sentence and
 * the builder groups on the id, never by matching the text.
 */

/** One publish issue. `node_id` is null for an issue about the flow as a whole (no entry node, a
 *  cycle spanning several nodes, a cap breached). */
export interface FlowIssue {
	node_id: string | null;
	message: string;
}

export interface GroupedIssues {
	/** node id → its issues, in the order the engine reported them. */
	byNode: Record<string, string[]>;
	/** Issues about the flow as a whole, and any naming a node that is no longer in the draft. */
	general: string[];
	/** Every issue as a flat list — the count for the "N problems" badge. */
	all: string[];
}

/**
 * Group issues by node. An issue naming a node the draft no longer contains falls into `general`
 * rather than vanishing: the alternative is an author who cannot publish and cannot see why,
 * because the only mention of the reason was attached to something deleted.
 */
export function groupIssues(issues: FlowIssue[], nodeIds: Iterable<string> = []): GroupedIssues {
	const known = new Set(nodeIds);
	const byNode: Record<string, string[]> = {};
	const general: string[] = [];
	const all: string[] = [];
	// One problem, one entry in `all`. The API files a sentence that spans several nodes (an orphan
	// set, a loop) against EVERY node in it so each of them badges, which would otherwise make the
	// "3 problems" count say 3 for one loop.
	const counted = new Set<string>();
	for (const issue of issues ?? []) {
		const message = issue?.message ?? '';
		if (!message) continue;
		if (!counted.has(message)) {
			counted.add(message);
			all.push(message);
		}
		const id = issue.node_id;
		if (id && (known.size === 0 || known.has(id))) (byNode[id] ??= []).push(message);
		else if (!general.includes(message)) general.push(message);
	}
	return { byNode, general, all };
}

/** True when this node has something wrong with it — the canvas badge's condition. */
export function nodeHasIssues(grouped: GroupedIssues, nodeId: string): boolean {
	return (grouped.byNode[nodeId]?.length ?? 0) > 0;
}

export const NO_ISSUES: GroupedIssues = { byNode: {}, general: [], all: [] };

// ── Asking the engine ───────────────────────────────────────────────────────────────────────

/** Where the browser asks. A same-origin SvelteKit endpoint rather than the API directly: the
 *  access token lives in a server-side session, and the builder must not hold one. */
export const VALIDATE_ENDPOINT = '/flows/validate';

/**
 * Ask the engine what it would refuse. Resolves to the issue list, or **null** when we could not
 * find out (offline, a 500, a session that expired mid-edit).
 *
 * Null is not an empty list, and the difference matters: an empty list means "this would publish"
 * and clears every badge on the canvas. Reporting that because a request failed would tell an
 * author their broken flow is fine.
 */
export async function validateDefinition(
	flowUid: string,
	definition: unknown,
	options: { fetch?: typeof globalThis.fetch; signal?: AbortSignal; endpoint?: string } = {}
): Promise<FlowIssue[] | null> {
	const doFetch = options.fetch ?? globalThis.fetch;
	try {
		const res = await doFetch(options.endpoint ?? VALIDATE_ENDPOINT, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ flow: flowUid, definition }),
			signal: options.signal
		});
		if (!res.ok) return null;
		const body = (await res.json()) as { issues?: FlowIssue[] };
		return Array.isArray(body?.issues) ? body.issues : [];
	} catch {
		return null;
	}
}

/** How long the author has to stop typing before we ask. Long enough that a sentence typed into a
 *  branch label is one request, short enough that a badge feels like a reaction. */
export const VALIDATE_DEBOUNCE_MS = 500;

export interface ValidationRunner {
	/** The draft changed. Coalesces with anything already pending. */
	request: (definition: unknown) => void;
	/** Ask now (the Publish button's pre-flight), bypassing the debounce. Pass a definition to
	 *  validate that one; otherwise the last one `request` was given. */
	flush: (definition?: unknown) => Promise<void>;
	/** Stop: cancel the timer and ignore any reply still in flight. */
	dispose: () => void;
}

/**
 * A debounced, latest-wins validator.
 *
 * Two things it has to get right, both of which are the kind of bug you only see on a slow link:
 *
 * * **Latest wins, not last-to-arrive.** Every run carries a sequence number and a reply from an
 *   older run is dropped. Without it, a slow validation of the draft the author has already fixed
 *   lands after a fast one and re-badges nodes that are now clean.
 * * **A failure changes nothing.** `validateDefinition` returns null on any failure and the runner
 *   simply does not call back, leaving the last known-good badges in place.
 */
export function createValidationRunner(
	flowUid: string,
	onresult: (issues: FlowIssue[]) => void,
	options: {
		fetch?: typeof globalThis.fetch;
		endpoint?: string;
		delay?: number;
		onpending?: (pending: boolean) => void;
	} = {}
): ValidationRunner {
	const delay = options.delay ?? VALIDATE_DEBOUNCE_MS;
	let timer: ReturnType<typeof setTimeout> | null = null;
	let latest: unknown = null;
	let sequence = 0;
	let settled = 0;
	let disposed = false;

	async function run(): Promise<void> {
		if (disposed) return;
		const mine = ++sequence;
		options.onpending?.(true);
		const issues = await validateDefinition(flowUid, latest, {
			fetch: options.fetch,
			endpoint: options.endpoint
		});
		// `mine < settled` — a newer run has already reported; its answer is about a later draft.
		if (disposed || mine < settled) return;
		settled = mine;
		if (sequence === mine) options.onpending?.(false);
		if (issues !== null) onresult(issues);
	}

	return {
		request(definition: unknown) {
			if (disposed) return;
			latest = definition;
			if (timer) clearTimeout(timer);
			timer = setTimeout(() => {
				timer = null;
				void run();
			}, delay);
		},
		async flush(definition?: unknown) {
			if (disposed) return;
			if (definition !== undefined) latest = definition;
			if (timer) {
				clearTimeout(timer);
				timer = null;
			}
			await run();
		},
		dispose() {
			disposed = true;
			if (timer) clearTimeout(timer);
			timer = null;
		}
	};
}
