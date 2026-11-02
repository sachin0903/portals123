<script lang="ts">
	// THE EMAIL CHROME, built from `better-svelte-email`'s layout primitives — the card, its accent
	// edge, the masthead slot, the heading, the body slot, the CTA slot and the footer slot.
	//
	// Rendered at BUILD time into `apps/api/app/email/chrome_generated.py` (see
	// `pkgs/ui/scripts/build-email-chrome.mjs`), because the send path is Python and the kit is a
	// Svelte renderer. Every value the send fills in arrives here as a SENTINEL string that the
	// generator rewrites into a Python placeholder — so this file is the chrome's shape and holds no
	// brand, copy or colour of its own.
	//
	// WHY `Row`/`Column` AND NOT `Section`/`Container`/`Button`: those three are opinionated. `Section`
	// puts padding on its `<table>` where Word honours it only on the cell; `Container` merges its own
	// `max-width:37.5em` with ours so two conflicting declarations ship; `Button` styles the `<a>`,
	// which Word ignores, and emits no class for the `MSO_CTA_INK` conditional stylesheet to hook.
	// `Row` (a table) and `Column` (a `<td>`) both spread `...restProps`, so the padding, the widths
	// and the editor's `data-gjs-name` all land where they have to.
	//
	// THE ONE THING THE KIT CANNOT DO: there is no primitive that renders a bare `<tr>`, so a table
	// cannot carry three sibling rows. The Python chrome was two tables (the outer centring table and
	// the card, whose tbody held the accent bar, the content and the footer); this is necessarily one
	// table PER ROW, six in a delivered email against three. Word's table handling is why the Python
	// kept the nesting shallow, so it is a real cost rather than a formatting difference.
	//
	// CHECKED IN A REAL INBOX (Sep 2026) and it renders correctly — the card, the accent bar, the
	// pill and the footer hairline all match what the Python emitted. The rounded button corners in
	// that render mean it was a BROWSER-engine Outlook (new Outlook / on the web); classic Outlook
	// for Windows draws HTML with Word, which ignores `border-radius` and is the one engine the extra
	// nesting could still trouble. If it ever does, the fallback is known and cheap: hand the CARD
	// back to Python and keep the kit for the button and the footer, which are byte-comparable.
	import { Row, Column } from '@better-svelte-email/components';

	let {
		card,
		fontStack,
		ink,
		bodyInk,
		cardWidth,
		accentBarStyle,
		wordmark,
		heading,
		body,
		cta,
		footer,
		names = false
	}: {
		card: string;
		fontStack: string;
		ink: string;
		bodyInk: string;
		cardWidth: string;
		accentBarStyle: string;
		/** Sentinels the generator rewrites into Python placeholders. */
		wordmark: string;
		heading: string;
		body: string;
		cta: string;
		footer: string;
		/** The editor shape stamps `data-gjs-name`; the send shape omits the attribute entirely. */
		names?: boolean;
	} = $props();

	// `undefined` rather than '' — Svelte omits an attribute whose value is undefined, so the send
	// shape carries no trace of the editor's naming.
	const n = (name: string): string | undefined => (names ? name : undefined);
</script>

<Row
	data-gjs-name={n('Email card')}
	style="width:100%;border-collapse:collapse"
	width="100%"
	border={0}
	cellpadding="0"
	cellspacing="0"
>
	<Column data-gjs-name={n('Card wrapper')} align="center" style="padding:32px 12px">
		<Row
			data-gjs-name={n('Card')}
			width={cardWidth}
			border={0}
			cellpadding="0"
			cellspacing="0"
			style="width:100%;max-width:{cardWidth}px;border-collapse:collapse;background-color:{card};border-radius:10px"
		>
			<Column data-gjs-name={n('Card body')} style="padding:0">
				<!-- ONE TABLE PER ROW, for the reason the header gives: the kit has no bare-`<tr>`
				     primitive, so the accent bar, the content and the footer cannot be three rows of the
				     card's own tbody. -->
				<Row
					data-gjs-name={n('Accent bar row')}
					width="100%"
					border={0}
					cellpadding="0"
					cellspacing="0"
					style="border-collapse:collapse"
				>
					<!-- The cell must not be empty or a client collapses the 4px band away; a non-breaking
					     space as an expression, because `&nbsp;` alone is a text child the kit's `children`
					     type refuses. -->
					<Column data-gjs-name={n('Accent bar')} style={accentBarStyle}>{' '}</Column>
				</Row>
				<Row
					data-gjs-name={n('Card row')}
					width="100%"
					border={0}
					cellpadding="0"
					cellspacing="0"
					style="border-collapse:collapse"
				>
					<Column
						data-gjs-name={n('Card content')}
						style="padding:32px 32px 36px;font-family:{fontStack};color:{ink}"
					>
						{wordmark}
						<h1
							data-gjs-name={n('Heading')}
							style="margin:0 0 14px;font-size:22px;line-height:1.3;font-weight:700;letter-spacing:-0.01em;color:{ink}"
						>
							{heading}
						</h1>
						<div style="font-size:15px;line-height:1.65;color:{bodyInk}">{body}</div>
						{cta}
					</Column>
				</Row>
				{footer}
			</Column>
		</Row>
	</Column>
</Row>
