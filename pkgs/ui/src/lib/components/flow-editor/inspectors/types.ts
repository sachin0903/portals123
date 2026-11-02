/**
 * The per-kind inspector contract. The canvas selects a node; `NodeInspector` dispatches on its
 * `kind` to one of these; each one edits the node in place and reports what it changed.
 *
 * Editing in place (rather than returning a patch) is deliberate — it is what `StepInspector` has
 * always done, the node object is the same one the canvas holds in its `$state`, and a patch-merge
 * layer would only add a place for the two copies to disagree.
 *
 * The two callbacks are NOT the same event. `onchange` means "the node's contents changed" — save
 * the draft, re-render the node's chrome. `onrestructure` means "the graph's shape changed" — an
 * edge was re-pointed, a branch arm added or removed — so the canvas must re-derive its edges and
 * re-run the layout pass for anything unplaced. Calling the wrong one is how a re-wired edge stays
 * invisible until reload.
 */
import type { FlowNode, FlowForm, FlowDocument, FlowTemplate } from '@graphene/ui/book/journeys';

/** The other flows a `start_flow` node may hand over to. */
export interface FlowHandoffTarget {
	uid: string;
	code: string;
	name: string;
}

export interface InspectorProps {
	/** The selected node, edited in place. */
	node: FlowNode;
	/** Every node in the draft — the candidate targets for edge and prerequisite pickers. Includes
	 *  the selected node, so each picker excludes `node.id` itself. */
	nodes: FlowNode[];
	forms: FlowForm[];
	documents: FlowDocument[];
	templates: FlowTemplate[];
	/** Firm roles, for the raise-a-task and assign pickers; empty ⇒ free-text slug. */
	roles?: { slug: string; name: string }[];
	/** Other published flows, for `start_flow`. */
	flows?: FlowHandoffTarget[];
	/** Publish issues scoped to this node — rendered inline against the field at fault where the
	 *  inspector can tell which one that is, and as a list at the top otherwise. */
	issues?: string[];
	/** False for a read-only viewer (an archived version, or a user without `flows:write`). */
	canManage: boolean;
	/** The node's contents changed — persist the draft. */
	onchange: () => void;
	/** The graph's shape changed — re-derive edges and re-lay-out. */
	onrestructure: () => void;
}
