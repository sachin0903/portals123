/**
 * The canvas → edge/node channel.
 *
 * xyflow instantiates node and edge components itself, so the only props they get are the ones it
 * chooses to pass (`data`, `selected`, geometry). Anything else has to travel by context. Putting
 * the callbacks HERE rather than stuffing them into each edge's `data` keeps `buildGraph` a pure
 * projection — it maps a document to a picture and holds nobody's functions — and means adding a
 * new affordance to an edge is not a change to the shape of every edge in the graph.
 */
import { getContext, setContext } from 'svelte';
import type { FlowEdgeRef } from '@graphene/ui/book/journeys';

const KEY = Symbol('flow-editor');

export interface FlowEditorContext {
	/** False for a read-only viewer: no reconnect anchors, no disconnect buttons. */
	canManage: boolean;
	/** Unwire this socket, leaving both nodes in place. */
	disconnect: (ref: FlowEdgeRef) => void;
}

export function setFlowEditorContext(ctx: FlowEditorContext): void {
	setContext(KEY, ctx);
}

export function flowEditorContext(): FlowEditorContext {
	return (
		getContext<FlowEditorContext | undefined>(KEY) ?? {
			canManage: false,
			disconnect: () => {}
		}
	);
}
