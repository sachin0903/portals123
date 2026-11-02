// Live "which template variables have no source" check for the editor — the client-side companion
// of the backend lint (apps/api/app/routers/templates.py::unresolved_variables) and the generate
// gate. A template's AVAILABLE variables are: common render context (client/adviser/firm/today…),
// the attached form's fields, and datasets (the `datasets` map, `{% for %}` loop sources, and the
// loop-bound vars). Any other {{ token }} has no source and would render blank, so we surface it in
// the editor as the author types.
//
// This is a regex approximation (not a full Jinja parse) — deliberately: it's a live authoring
// hint, while the authoritative block is the server-side generate gate. It errs toward NOT flagging
// (unknown constructs are ignored) so it never nags about a valid token.

// Roots always present in the render context — keep in step with app/render/sample.py sample_context().
export const COMMON_ROOTS = [
	'client',
	'client_name',
	'client_email',
	'adviser',
	'adviser_name',
	'firm',
	'firm_name',
	'today',
	'date',
	'document_name',
	'answers',
	'datasets',
	// First row of each dataset for single-value bindings: `{{ one.<code>.<col> }}` (no loop).
	'one',
	// The client's own book: `accounts` is a list to loop (`{% for a in accounts %}`), `portfolio`
	// the roll-up for prose. Supplied per client at render by `worker.py::_client_accounts_context`
	// and previewed from `sample.py::_SAMPLE_ACCOUNTS`.
	'accounts',
	'portfolio'
];

/**
 * Collections the RENDER CONTEXT supplies per client, offered wherever a loop source is picked —
 * beside the firm's own datasets, because to an author "repeat over the client's accounts" and
 * "repeat over a dataset" are the same act.
 *
 * They are NOT datasets and are deliberately not registered as ones: there is no
 * `documents.datasets` row, no uid, no version and no `sample_data` behind them. They are resolved
 * from the client's own book at render (`worker.py::_client_accounts_context`) and from
 * `render/sample.py::_SAMPLE_ACCOUNTS` in a preview.
 *
 * `columns` mirrors those two, key for key. A column missing here is one the picker never offers and
 * the lint cannot column-check; a column here that the render does not supply reads as available and
 * renders blank — so the three move together.
 */
export const BUILTIN_COLLECTIONS: { code: string; name: string; columns: string[] }[] = [
	{
		code: 'accounts',
		name: "The client's accounts",
		columns: [
			'name',
			'number',
			'product',
			'product_type',
			'status',
			'value',
			'currency',
			'risk_level',
			'model',
			'fee_package',
			'opened',
			'value_as_of'
		]
	}
];

/**
 * Every collection a loop, table or chart may bind to: the built-ins first, then the firm's
 * datasets.
 *
 * Built-ins lead because a client document's commonest loop is over the client's own accounts, and
 * a firm with forty datasets would otherwise have it buried. A dataset that has taken a built-in's
 * code wins the lookup — it is the firm's own data under a name they chose, and silently resolving
 * their `accounts` dataset to ours would be the worse surprise; `.find` returns the first match, so
 * this filters the built-in out rather than relying on ordering.
 */
export function collectionOptions<T extends { code: string; name: string; columns: string[] }>(
	datasetOptions: T[]
): { code: string; name: string; columns: string[] }[] {
	const taken = new Set(datasetOptions.map((d) => d.code));
	return [
		...BUILTIN_COLLECTIONS.filter((c) => !taken.has(c.code)),
		...datasetOptions.map((d) => ({ code: d.code, name: d.name, columns: d.columns }))
	];
}

// Jinja globals/builtins a template may legitimately reference without a data source.
const JINJA_STATICS = ['loop', 'range', 'namespace'];

// The root identifier of an output token: `{{ client.name | title }}` → `client`. Skips tokens that
// open with a literal/number/quote (e.g. `{{ 'x' }}`), which have no variable root.
const TOKEN_ROOT_RE = /\{\{-?\s*([a-zA-Z_]\w*)/g;
// `{{ root.member … }}` — captures the root and its FIRST member access, for dataset-column checks.
const TOKEN_MEMBER_RE = /\{\{-?\s*([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)/g;
// `{% for a, b in source %}` — captures the loop target(s) and the source identifier's root.
const FOR_LOOP_RE = /\{%-?\s*for\s+([\w\s,]+?)\s+in\s+([a-zA-Z_]\w*)/gi;
// `{{ one.<code>.<col> … }}` — single-value (no-loop) binding: captures the dataset code + column so
// it can be column-checked (block 3) the same way loop-bound vars are.
const ONE_MEMBER_RE = /\{\{-?\s*one\.([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)/g;

/** The distinct unsourced tokens in `html`:
 *   1. unknown {{ root }} — variables with no home in the common context, the attached form fields,
 *      or the datasets the template loops over (returned as the bare root, e.g. `mystery_fee`);
 *   2. unknown dataset COLUMNS — `x.<col>` where a `{% for x in code %}` loop binds `x` to a dataset
 *      in `datasetColumns` whose columns exclude `<col>` (returned as `x.<col>`, e.g. `row.typo`).
 *  Column-checking is SOFT: a dataset with no known columns (a `SELECT *`, an unparsed source, or a
 *  multi-target/destructuring loop) is never column-checked, so valid tokens are never false-flagged.
 *  `datasetColumns` maps a dataset code → its known column names. Order-preserving. */
export function unsourcedTokens(
	html: string,
	formKeys: string[] = [],
	datasetColumns: Record<string, string[]> = {}
): string[] {
	if (!html) return [];
	const known = new Set<string>([...COMMON_ROOTS, ...JINJA_STATICS, ...formKeys]);

	// Loop sources are dataset codes by construction; loop targets are bound within the loop body.
	// Both are legitimate roots. Additionally, a SINGLE-target loop over a dataset with a known
	// column schema lets us column-check that bound variable's `.member` accesses (below).
	const loopVarColumns: Record<string, string[]> = {};
	FOR_LOOP_RE.lastIndex = 0;
	let loop: RegExpExecArray | null;
	while ((loop = FOR_LOOP_RE.exec(html))) {
		const targets = loop[1]!
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);
		const source = loop[2]!;
		for (const t of targets) known.add(t);
		known.add(source);
		if (targets.length === 1 && datasetColumns[source]?.length) {
			loopVarColumns[targets[0]!] = datasetColumns[source]!;
		}
	}

	const out: string[] = [];
	const seen = new Set<string>();

	// (1) Unknown roots.
	TOKEN_ROOT_RE.lastIndex = 0;
	let tok: RegExpExecArray | null;
	while ((tok = TOKEN_ROOT_RE.exec(html))) {
		const root = tok[1]!;
		if (!known.has(root) && !seen.has(root)) {
			seen.add(root);
			out.push(root);
		}
	}

	// (2) Unknown dataset columns — only for loop vars bound to a dataset whose columns we know.
	TOKEN_MEMBER_RE.lastIndex = 0;
	let mem: RegExpExecArray | null;
	while ((mem = TOKEN_MEMBER_RE.exec(html))) {
		const root = mem[1]!;
		const member = mem[2]!;
		const cols = loopVarColumns[root];
		if (!cols || cols.includes(member)) continue;
		const token = `${root}.${member}`;
		if (!seen.has(token)) {
			seen.add(token);
			out.push(token);
		}
	}

	// (3) Single-value bindings `{{ one.<code>.<col> }}` — the picker's most common path. Column-check
	// against the dataset's known columns when we have them, so a mistyped column is flagged like a
	// loop var's is. Soft: a SELECT-* / unknown-column dataset (not in `datasetColumns`) is skipped.
	ONE_MEMBER_RE.lastIndex = 0;
	let one: RegExpExecArray | null;
	while ((one = ONE_MEMBER_RE.exec(html))) {
		const cols = datasetColumns[one[1]!];
		if (!cols || !cols.length || cols.includes(one[2]!)) continue;
		const token = `one.${one[1]}.${one[2]}`;
		if (!seen.has(token)) {
			seen.add(token);
			out.push(token);
		}
	}
	return out;
}

/** Insert-catalogue groups for the tenant's datasets: each dataset with a known column schema
 *  contributes its columns as `row.<col>` tokens — the established `{% for row in <code> %}` loop
 *  convention (so they resolve inside a repeat/table that loops the dataset). Datasets with no known
 *  columns (a `SELECT *`, an as-yet-unparsed source) are skipped — we never guess column names.
 *
 *  We surface EVERY dataset the editor knows about, not just the template's `linked_datasets`:
 *  dataset binding in the editor is component-level (a chart's `data-comp-data-source`, a data
 *  table's `data-dataset`, or a bare `{% for %}` loop source) and doesn't populate `linked_datasets`
 *  (that panel is managed on the template detail page), so keying off it would leave the picker
 *  empty. The column-aware lint (`unsourcedTokens`) validates each `row.<col>` against the loop's
 *  actual dataset, so offering a superset here is safe. Each group is labelled by the dataset name. */
export function datasetVariableGroups(
	datasetOptions: { code: string; name: string; columns: string[] }[]
): { group: string; vars: { key: string; label: string }[] }[] {
	const groups: { group: string; vars: { key: string; label: string }[] }[] = [];
	for (const ds of datasetOptions) {
		if (!ds.columns.length) continue;
		const name = ds.name || ds.code;
		// Single value, no loop: `{{ one.<code>.<col> }}` — the common case for a subject dataset
		// (one row per client). Listed first because it's what most bindings want.
		groups.push({
			group: `${name} — single value`,
			vars: ds.columns.map((c) => ({ key: `one.${ds.code}.${c}`, label: c }))
		});
		// For use inside a `{% for row in <code> %}` loop (repeat/table over the whole dataset). The
		// header spells out the loop requirement so authors don't drop a bare `row.*` into prose,
		// where it renders blank (the lint flags the stray `row` root when they do).
		groups.push({
			group: `${name} — row field (inside a repeat loop)`,
			vars: ds.columns.map((c) => ({ key: `row.${c}`, label: c }))
		});
	}
	return groups;
}
