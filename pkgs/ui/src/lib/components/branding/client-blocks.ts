/**
 * The client-portal block catalogue — the fixed set of blocks a firm can place on portal pages in
 * the branding studio. Everything here pins to ./block-contract.json, the single Python/TS contract:
 * the API (app/branding.py) validates the same type/variant/setting allowlists on write, and
 * ./registry.test.ts + apps/api's test_branding.py both assert against the JSON, so the two sides
 * cannot drift silently. Labels/descriptions are the human-facing copy shown in the builder.
 */
import type { BrandingBlock } from '@graphene/auth';
import contract from './block-contract.json';

/** The eleven arrangeable client-portal block types (matches the contract + API allowlist). */
export type BlockType =
	| 'portfolioHeader'
	| 'accounts'
	| 'allocation'
	| 'holdings'
	| 'activity'
	| 'adviser'
	| 'news'
	| 'documents'
	| 'actionItems'
	| 'onboardingProgress'
	| 'message';

/** The backend reads a block needs. Drives the renderer's per-page data fetch (load only what the
 * page's blocks use) and the studio's "where this comes from" hints. */
export type DataSource =
	| 'portfolio'
	| 'accounts'
	| 'holdings'
	| 'activity'
	| 'documents'
	| 'tasks'
	| 'applications'
	| 'adviser'
	| 'context'
	| 'content';

/** One editable block setting — declarative, so the studio inspector renders controls from data
 * instead of hand-built forms. `key` is snake_case because settings ride the wire exactly as
 * pydantic dumps them (see the contract's $comment). */
export interface SettingDescriptor {
	key: string;
	label: string;
	help?: string;
	control: 'segmented' | 'select' | 'switch' | 'number' | 'text' | 'textarea';
	options?: { value: string; label: string }[];
	min?: number;
	max?: number;
	maxLength?: number;
	/**
	 * switch only: what an ABSENT key renders as, so the inspector control agrees with the
	 * widget instead of lying about the page. Omitted ⇒ true — the historic switches
	 * (show_chart/show_sparklines/hide_when_empty) all read `!== false`; the listing toolbars
	 * read `=== true` and declare `defaultOn: false`.
	 */
	defaultOn?: boolean;
}

export interface BlockMeta {
	type: BlockType;
	label: string;
	description: string;
	/** Default column span (1–12) + row span (1–6) on the 12-column responsive grid. */
	defaultW: number;
	defaultH: number;
	/**
	 * Narrowest column span the block stays usable at — the builder refuses to resize below it
	 * (layout-builder clamps steppers, corner drags and adds through clampBlockSpan). This is a
	 * UI-plane concern only: it is deliberately NOT in block-contract.json, and the renderer
	 * still draws whatever span a stored layout carries (the container-query layouts degrade
	 * gracefully) — the floor stops NEW footgun layouts being authored, it doesn't break old ones.
	 */
	minW: number;
	/** The reads this block depends on (empty ⇒ renders from branding/settings alone). */
	sources: DataSource[];
	/** The type's variant allowlist, 'auto' first — sourced from the contract so it CANNOT drift;
	 * registry.test.ts still pins the equality in case this ever regresses to a literal. */
	variants: string[];
	defaultVariant: string;
	/** Inspector controls; keys ⊆ the contract's settings for the type (registry.test.ts). */
	settings: SettingDescriptor[];
}

/** Column/row span bounds (mirrors the API clamps in app/branding.py Block). */
export const MAX_COLS = 12;
export const MAX_ROWS = 6;

// A `variant` control for a multi-variant type: 'Auto' + the concrete looks, in contract order.
function variantSetting(type: BlockType, labels: Record<string, string>): SettingDescriptor {
	return {
		key: 'variant',
		label: 'Style',
		help: 'Auto picks a style from the client’s own data.',
		control: 'segmented',
		options: contract.blocks[type].variants.map((v) => ({
			value: v,
			label: labels[v] ?? v.charAt(0).toUpperCase() + v.slice(1)
		}))
	};
}

// The contract JSON's inferred type is per-key structural (only SOME blocks carry `looks`), so
// the optional axis reads through this uniform view; registry.test.ts pins it to the real file.
const CONTRACT_BLOCKS = contract.blocks as Record<
	string,
	{ variants: string[]; looks?: string[]; settings: string[] }
>;

// A `look` control for a looks-bearing type: 'Auto' + the type's visual designs, in contract
// order. Look is orthogonal to variant — variant is the DATA shape (stack vs table), look is the
// chrome (card vs the type's page-ground design). 'Auto' follows the portal finish axis
// (Branding.finish → looks.ts): card finish keeps the carded design, ledger resolves to the
// type's chrome-less signature.
function lookSetting(type: BlockType, labels: Record<string, string> = {}): SettingDescriptor {
	return {
		key: 'look',
		label: 'Look',
		help: 'Auto follows the portal finish — carded by default, this block’s open page-ground design under the ledger finish.',
		control: 'segmented',
		options: (CONTRACT_BLOCKS[type]?.looks ?? []).map((v) => ({
			value: v,
			label: labels[v] ?? v.charAt(0).toUpperCase() + v.slice(1)
		}))
	};
}

/** Catalogue of every client-portal block, in the builder's default order + footprint. */
export const CLIENT_BLOCKS: BlockMeta[] = [
	{
		type: 'portfolioHeader',
		label: 'Portfolio header',
		description: 'Total value, performance and the headline return.',
		defaultW: 12,
		defaultH: 1,
		// The headline value + return stay legible down to a third of the grid; narrower and the
		// figure itself wraps.
		minW: 4,
		sources: ['portfolio'],
		variants: contract.blocks.portfolioHeader.variants,
		defaultVariant: 'auto',
		settings: [
			variantSetting('portfolioHeader', {}),
			{ key: 'show_chart', label: 'Performance chart', control: 'switch' },
			{
				key: 'actions',
				label: 'Actions',
				help: 'The buttons shown beside the total.',
				control: 'segmented',
				options: [
					{ value: 'deposit_withdraw', label: 'Deposit & withdraw' },
					{ value: 'contact', label: 'Contact adviser' },
					{ value: 'none', label: 'None' }
				]
			},
			{
				key: 'default_period',
				label: 'Default period',
				help: 'The performance window clients land on.',
				control: 'segmented',
				// '6M' leads on purpose: the inspector highlights the FIRST option when the setting
				// is unset (pages-view.ts inspectorValue), and unset genuinely renders 6M — the
				// registry convention is first-slot-is-the-default ('auto' leads variant, and
				// 'deposit_withdraw' leads actions above). Chronological order here would show a
				// firm '1M' selected while its clients get 6M.
				options: [
					{ value: '6M', label: '6M' },
					{ value: '1M', label: '1M' },
					{ value: '3M', label: '3M' },
					{ value: '1Y', label: '1Y' }
				]
			}
		]
	},
	{
		type: 'accounts',
		label: 'Accounts',
		// Wrapper data currently rides the /crm/home payload (portfolio source), not its own read.
		description: 'The stack of ISA, SIPP and other account wrappers.',
		defaultW: 6,
		defaultH: 1,
		// Table-bearing (the 'table' variant): even with container-collapsed columns, a listing
		// below half the grid is name + one figure and nothing else.
		minW: 6,
		sources: ['portfolio'],
		variants: contract.blocks.accounts.variants,
		defaultVariant: 'auto',
		settings: [
			variantSetting('accounts', {}),
			lookSetting('accounts'),
			{ key: 'limit', label: 'Accounts shown', control: 'number', min: 1, max: 50 },
			{
				key: 'group_by',
				label: 'Group by',
				help: 'Owner puts each family member’s accounts under their own name. Stack and cards group; the summary tiles re-axis.',
				control: 'select',
				options: [
					{ value: 'none', label: 'No grouping' },
					{ value: 'wrapper', label: 'Wrapper type' },
					{ value: 'owner', label: 'Owner' }
				]
			},
			{
				key: 'show_sparklines',
				label: 'Sparklines',
				help: 'The trend line on stack cards.',
				control: 'switch'
			}
		]
	},
	{
		type: 'allocation',
		label: 'Allocation',
		description: 'Asset-class split across the portfolio.',
		defaultW: 6,
		defaultH: 1,
		// The donut plus its centre total need roughly a third of the grid before the ring
		// becomes a decorative dot.
		minW: 4,
		sources: ['portfolio'],
		variants: contract.blocks.allocation.variants,
		defaultVariant: 'auto',
		settings: [variantSetting('allocation', {}), lookSetting('allocation')]
	},
	{
		type: 'holdings',
		label: 'Holdings',
		description: 'Individual funds and positions held.',
		defaultW: 8,
		defaultH: 2,
		// Table-bearing: fund names are long ('Vanguard FTSE Developed World ex-UK…') — below
		// half the grid the listing is all ellipsis.
		minW: 6,
		sources: ['holdings'],
		variants: contract.blocks.holdings.variants,
		defaultVariant: 'auto',
		settings: [
			variantSetting('holdings', { top: 'Top holdings', grouped: 'By asset class' }),
			lookSetting('holdings', { bars: 'Value bars' }),
			{ key: 'limit', label: 'Holdings shown', control: 'number', min: 1, max: 25 },
			{
				key: 'toolbar',
				label: 'Search & filters',
				help: 'A search box and account filter above the listing.',
				control: 'switch',
				defaultOn: false
			}
		]
	},
	{
		type: 'activity',
		label: 'Activity',
		description: 'Recent deposits, dividends, trades and fees.',
		defaultW: 6,
		defaultH: 2,
		// Table-bearing: date + description + amount is already three columns.
		minW: 6,
		sources: ['activity'],
		variants: contract.blocks.activity.variants,
		defaultVariant: 'auto',
		settings: [
			variantSetting('activity', {}),
			lookSetting('activity'),
			{ key: 'limit', label: 'Entries shown', control: 'number', min: 1, max: 50 },
			{
				key: 'toolbar',
				label: 'Search & filters',
				help: 'A search box and account filter above the listing.',
				control: 'switch',
				defaultOn: false
			}
		]
	},
	{
		type: 'adviser',
		label: 'Adviser',
		description: "The client's adviser and how to reach them.",
		defaultW: 4,
		defaultH: 1,
		// Avatar + name + three action buttons; at 2 the buttons stack into a tower.
		minW: 3,
		sources: ['context'],
		variants: contract.blocks.adviser.variants,
		defaultVariant: 'auto',
		settings: [lookSetting('adviser')]
	},
	{
		type: 'news',
		label: 'News',
		description: 'Curated market and planning updates.',
		defaultW: 4,
		defaultH: 2,
		// Headline cards read fine narrow — prose wraps.
		minW: 2,
		sources: ['content'],
		variants: contract.blocks.news.variants,
		defaultVariant: 'auto',
		settings: [
			variantSetting('news', { feed: 'Headlines', library: 'Library' }),
			lookSetting('news'),
			{ key: 'limit', label: 'Items shown', control: 'number', min: 1, max: 12 }
		]
	},
	{
		type: 'documents',
		label: 'Documents',
		description: 'Statements, illustrations and agreements.',
		defaultW: 12,
		defaultH: 2,
		// Table-bearing (the listing rows carry name + category + date + size).
		minW: 6,
		sources: ['documents'],
		variants: contract.blocks.documents.variants,
		defaultVariant: 'auto',
		settings: [
			variantSetting('documents', {}),
			lookSetting('documents', { timeline: 'By month' }),
			{ key: 'limit', label: 'Documents shown', control: 'number', min: 1, max: 12 },
			{
				key: 'toolbar',
				label: 'Search & filters',
				help: 'A search box and category filter pills above the listing.',
				control: 'switch',
				defaultOn: false
			},
			{
				key: 'show_requests',
				label: 'Outstanding requests banner',
				help: 'Shows how many document requests are awaiting the client, linking to /requests.',
				control: 'switch',
				defaultOn: false
			}
		]
	},
	{
		type: 'actionItems',
		label: 'Action items',
		description: 'Outstanding tasks and requests assigned to the client.',
		defaultW: 12,
		defaultH: 1,
		// A vertical checklist — wraps gracefully at any width.
		minW: 2,
		sources: ['tasks'],
		variants: contract.blocks.actionItems.variants,
		defaultVariant: 'auto',
		settings: [
			lookSetting('actionItems'),
			{ key: 'limit', label: 'Items shown', control: 'number', min: 1, max: 10 },
			{ key: 'hide_when_empty', label: 'Hide when empty', control: 'switch' }
		]
	},
	{
		type: 'onboardingProgress',
		label: 'Onboarding progress',
		description: 'Where the client is in onboarding. Hidden once complete.',
		defaultW: 12,
		defaultH: 1,
		// A progress bar + step copy — shrinks fine.
		minW: 2,
		sources: ['applications'],
		variants: contract.blocks.onboardingProgress.variants,
		defaultVariant: 'auto',
		settings: [lookSetting('onboardingProgress')]
	},
	{
		type: 'message',
		label: 'Message',
		description: 'A note from the firm, shown verbatim.',
		defaultW: 12,
		defaultH: 1,
		// Free prose — wraps at any width.
		minW: 2,
		sources: [],
		variants: contract.blocks.message.variants,
		defaultVariant: 'auto',
		settings: [
			lookSetting('message'),
			{ key: 'title', label: 'Title', control: 'text', maxLength: 80 },
			{ key: 'body', label: 'Body', control: 'textarea', maxLength: 1000 }
		]
	}
];

/** Quick lookup of a block's metadata by type. */
export const CLIENT_BLOCK_BY_TYPE: Record<string, BlockMeta> = Object.fromEntries(
	CLIENT_BLOCKS.map((b) => [b.type, b])
);

/** A block's effective column span — its stored `w`, else the per-type default, clamped 1–12. */
export function blockW(b: BrandingBlock): number {
	const w = b.w ?? CLIENT_BLOCK_BY_TYPE[b.type]?.defaultW ?? MAX_COLS;
	return Math.max(1, Math.min(MAX_COLS, w));
}

/** A block's effective row span — its stored `h`, else the per-type default, clamped 1–6. */
export function blockH(b: BrandingBlock): number {
	const h = b.h ?? CLIENT_BLOCK_BY_TYPE[b.type]?.defaultH ?? 1;
	return Math.max(1, Math.min(MAX_ROWS, h));
}

/** A type's minimum usable column span (1 for unknown types — no floor we can vouch for). */
export function minBlockW(type: string): number {
	return CLIENT_BLOCK_BY_TYPE[type]?.minW ?? 1;
}

/** A page-unique id: the bare type, then '{type}-2', …. Pure — the caller records it in `taken`. */
export function mintBlockId(type: string, taken: Set<string>): string {
	if (!taken.has(type)) return type;
	let n = 2;
	while (taken.has(`${type}-${n}`)) n += 1;
	return `${type}-${n}`;
}

/** The one identity the renderer's cells, the builder's tiles and the measured-height report key by.
 *  API-authored layouts carry no ids, and two id-less blocks of a type are otherwise a duplicate key in a keyed `{#each}` and a measurement on the wrong tile. */
export function blockKeys(blocks: readonly BrandingBlock[]): string[] {
	const taken = new Set<string>();
	return blocks.map((b) => {
		const id = b.id && !taken.has(b.id) ? b.id : mintBlockId(b.type, taken);
		taken.add(id);
		return id;
	});
}

/**
 * Clamp a requested resize to the type's usable range: width to [minW, 12], height to [1, 6].
 * This is the builder's single resize gate (steppers, corner drag and add all pass through it) —
 * kept pure here so registry.test.ts can pin the floor without mounting the builder. The
 * RENDERER deliberately does not use it: a stored layout below today's floor still draws
 * (see BlockMeta.minW).
 */
export function clampBlockSpan(type: string, w: number, h: number): { w: number; h: number } {
	return {
		w: Math.max(minBlockW(type), Math.min(MAX_COLS, w)),
		h: Math.max(1, Math.min(MAX_ROWS, h))
	};
}

// The pre-pages home blocks, in their pre-pages order. defaultClientLayout() must keep emitting
// exactly these — the legacy `Branding.layout` mirror feeds today's home renderer, which has no
// components for the three newer types yet (they arrive with the page renderer in phase 2).
const LEGACY_HOME_TYPES: ReadonlySet<BlockType> = new Set([
	'portfolioHeader',
	'accounts',
	'allocation',
	'holdings',
	'activity',
	'adviser',
	'news',
	'documents'
]);

/** The default legacy home layout: the original eight blocks, sensible order + footprint, all
 * visible. Kept for the legacy `Branding.layout` mirror path; the pages world starts from
 * `defaultPages()` (portal-pages.ts) instead. */
export function defaultClientLayout(): BrandingBlock[] {
	return CLIENT_BLOCKS.filter((b) => LEGACY_HOME_TYPES.has(b.type)).map((b) => ({
		type: b.type,
		visible: true,
		w: b.defaultW,
		h: b.defaultH
	}));
}
