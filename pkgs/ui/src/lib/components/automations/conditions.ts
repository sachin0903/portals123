/**
 * The condition builder's model, and its round trip to the authored JSON grammar.
 *
 * The stored grammar (flow-contract.json → `conditions`) is a tree of leaves
 * `{field, operator, value}` and groups `{logic, conditions}`. That is a fine thing to evaluate and
 * a poor thing to edit: a row needs a stable identity while you are dragging its dropdowns around,
 * an operator's polarity is an is/is-not TOGGLE rather than two entries in a list, and every value
 * an input holds is a string regardless of whether the grammar wants a number, a list or a date.
 *
 * So the builder edits a MODEL and this module converts, in both directions, with no evaluation
 * logic of its own — there is exactly one evaluator (`app/flows/conditions.py`) and a second one
 * living in the browser is precisely the drift the shared contract exists to prevent. Anything the
 * builder wants to know about whether a condition HOLDS comes from the dry run.
 *
 * Every mutation returns a NEW tree rather than mutating in place. That is not stylistic: these
 * models are held in Svelte `$state`, `structuredClone` throws on a state proxy, and in-place
 * mutation of a nested node is exactly where that reflex gets reached for.
 */
import { OPERATOR_SPECS, type OperatorSpec } from './contract.js';
import type { ConditionJson } from './types.js';

export interface BuilderLeaf {
	id: string;
	kind: 'leaf';
	field: string;
	/** The POSITIVE operator; polarity rides on `negate`. */
	op: string;
	negate: boolean;
	/** Always a string — what the input holds. Coerced per operator on serialisation. */
	value: string;
}

export interface BuilderGroup {
	id: string;
	kind: 'group';
	logic: 'and' | 'or';
	children: BuilderNode[];
}

export type BuilderNode = BuilderLeaf | BuilderGroup;

let counter = 0;

/** Model-local ids. Never serialised — they exist so a row survives a re-render mid-edit. */
export function nextId(prefix = 'n'): string {
	counter += 1;
	return `${prefix}${counter}`;
}

export function blankLeaf(): BuilderLeaf {
	return { id: nextId('l'), kind: 'leaf', field: '', op: 'equals', negate: false, value: '' };
}

export function blankGroup(logic: 'and' | 'or' = 'and'): BuilderGroup {
	return { id: nextId('g'), kind: 'group', logic, children: [blankLeaf()] };
}

export function emptyRoot(): BuilderGroup {
	return { id: nextId('g'), kind: 'group', logic: 'and', children: [] };
}

// ── JSON → model ────────────────────────────────────────────────────────────────────────────

function isGroupJson(node: Record<string, unknown>): boolean {
	return 'logic' in node || 'conditions' in node;
}

/** The string an input should show for a stored value, per the operator's value kind. */
export function valueToInput(value: unknown, spec: OperatorSpec | undefined): string {
	if (value === null || value === undefined) return '';
	if (spec?.value === 'list') {
		return Array.isArray(value) ? value.map((v) => String(v)).join(', ') : String(value);
	}
	if (typeof value === 'object') return JSON.stringify(value);
	return String(value);
}

function leafFromJson(node: Record<string, unknown>): BuilderLeaf {
	const rawOp = String(node.operator ?? node.op ?? 'equals');
	const spec = OPERATOR_SPECS[rawOp];
	// An operator outside the contract keeps its raw name and is treated as positive — the row
	// stays visible and editable rather than silently becoming `equals`, which would rewrite a
	// definition the author never touched.
	return {
		id: nextId('l'),
		kind: 'leaf',
		field: typeof node.field === 'string' ? node.field : '',
		op: spec?.negate ? (spec.twin ?? rawOp) : rawOp,
		negate: spec?.negate ?? false,
		value: valueToInput(node.value, spec)
	};
}

/**
 * Parse stored conditions into the builder's model. The root is ALWAYS a group, even for a single
 * stored leaf, so the surface has somewhere to hang the AND/OR control and the "add condition"
 * button without a special case for "the first one".
 */
export function toModel(json: ConditionJson): BuilderGroup {
	if (!json || typeof json !== 'object' || Object.keys(json).length === 0) return emptyRoot();
	const node = json as Record<string, unknown>;
	if (!isGroupJson(node)) {
		return { id: nextId('g'), kind: 'group', logic: 'and', children: [leafFromJson(node)] };
	}
	return groupFromJson(node);
}

function groupFromJson(node: Record<string, unknown>): BuilderGroup {
	const rawLogic = String(node.logic ?? 'and').toLowerCase();
	const members = Array.isArray(node.conditions) ? node.conditions : [];
	return {
		id: nextId('g'),
		kind: 'group',
		logic: rawLogic === 'or' ? 'or' : 'and',
		children: members
			.filter((m): m is Record<string, unknown> => typeof m === 'object' && m !== null)
			.map((m) => (isGroupJson(m) ? groupFromJson(m) : leafFromJson(m)))
	};
}

// ── Model → JSON ────────────────────────────────────────────────────────────────────────────

/** Coerce an input string to the shape the grammar wants for this operator. */
export function inputToValue(input: string, spec: OperatorSpec | undefined): unknown {
	const trimmed = input.trim();
	switch (spec?.value) {
		case 'none':
			return undefined;
		case 'list':
			return trimmed
				? trimmed
						.split(',')
						.map((part) => part.trim())
						.filter(Boolean)
				: [];
		case 'number':
		case 'days': {
			const n = Number(trimmed);
			// A non-numeric entry rides out AS TEXT rather than as NaN: the API's own
			// `condition_issues` then refuses it with a sentence about needing a number, which is a
			// better error than a silently-null value that quietly matches nothing.
			return trimmed !== '' && Number.isFinite(n) ? n : trimmed;
		}
		default:
			return trimmed;
	}
}

function leafToJson(leaf: BuilderLeaf): Record<string, unknown> | null {
	if (!leaf.field.trim()) return null; // a half-built row is not a condition
	const spec = OPERATOR_SPECS[leaf.op];
	const operator = leaf.negate && spec?.twin ? spec.twin : leaf.op;
	const out: Record<string, unknown> = { field: leaf.field.trim(), operator };
	const value = inputToValue(leaf.value, spec);
	if (value !== undefined) out.value = value;
	return out;
}

function groupToJson(group: BuilderGroup): Record<string, unknown> | null {
	const children = group.children
		.map((child) => (child.kind === 'leaf' ? leafToJson(child) : groupToJson(child)))
		.filter((c): c is Record<string, unknown> => c !== null);
	if (children.length === 0) return null;
	// A group of one is the same condition without the wrapper — emitting it keeps the round trip
	// stable (`toModel(toJson(m))` gives back the same shape) and keeps stored definitions from
	// accumulating a layer of nesting per save.
	if (children.length === 1) return children[0] ?? null;
	return { logic: group.logic, conditions: children };
}

/** Serialise the model. `null` means "unconditional", which is what the column stores. */
export function toJson(root: BuilderGroup): ConditionJson {
	return groupToJson(root);
}

/** The count of complete (field-bearing) leaves — what "3 conditions" in the UI means. */
export function leafCount(node: BuilderNode): number {
	if (node.kind === 'leaf') return node.field.trim() ? 1 : 0;
	return node.children.reduce((sum, child) => sum + leafCount(child), 0);
}

// ── Mutations (pure) ────────────────────────────────────────────────────────────────────────

function mapTree(node: BuilderNode, fn: (n: BuilderNode) => BuilderNode): BuilderNode {
	const mapped = fn(node);
	if (mapped.kind !== 'group') return mapped;
	return { ...mapped, children: mapped.children.map((child) => mapTree(child, fn)) };
}

/** Replace one node's fields by id. Unknown id ⇒ the tree comes back untouched. */
export function updateNode(
	root: BuilderGroup,
	id: string,
	patch: Partial<BuilderLeaf> & Partial<BuilderGroup>
): BuilderGroup {
	return mapTree(root, (node) =>
		node.id === id ? ({ ...node, ...patch } as BuilderNode) : node
	) as BuilderGroup;
}

/** Append a child to the named group (the root when `groupId` is the root's id). */
export function addChild(root: BuilderGroup, groupId: string, child: BuilderNode): BuilderGroup {
	return mapTree(root, (node) =>
		node.kind === 'group' && node.id === groupId
			? { ...node, children: [...node.children, child] }
			: node
	) as BuilderGroup;
}

/** Remove a node by id. The root itself is never removable. */
export function removeNode(root: BuilderGroup, id: string): BuilderGroup {
	if (root.id === id) return root;
	const prune = (group: BuilderGroup): BuilderGroup => ({
		...group,
		children: group.children
			.filter((child) => child.id !== id)
			.map((child) => (child.kind === 'group' ? prune(child) : child))
	});
	return prune(root);
}

/**
 * Flip a leaf's polarity. A no-op for an operator with no negated twin (`greaterThan` and friends
 * are not complements — equality is false for both — so there is nothing honest to flip TO, and the
 * builder hides the toggle for them).
 */
export function togglePolarity(root: BuilderGroup, id: string): BuilderGroup {
	return mapTree(root, (node) => {
		if (node.id !== id || node.kind !== 'leaf') return node;
		const spec = OPERATOR_SPECS[node.op];
		if (!spec?.twin) return node;
		return { ...node, negate: !node.negate };
	}) as BuilderGroup;
}

/**
 * Change a leaf's operator, keeping the value only when the new operator can still read it.
 * Switching "is" to "is one of" keeps the text (it becomes a one-item list); switching to "is
 * empty" drops it, because a value field that no longer renders must not keep a stale value that
 * would be silently serialised.
 */
export function setOperator(root: BuilderGroup, id: string, op: string): BuilderGroup {
	return mapTree(root, (node) => {
		if (node.id !== id || node.kind !== 'leaf') return node;
		const next = OPERATOR_SPECS[op];
		const keep = next && next.value !== 'none';
		return { ...node, op, negate: next?.twin ? node.negate : false, value: keep ? node.value : '' };
	}) as BuilderGroup;
}
