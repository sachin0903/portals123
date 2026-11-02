<script lang="ts">
	// The note field shared by the client record and the transfer detail. One composer, so the
	// darker field and the ghost icon cannot drift apart. The icon submits; it is not a second
	// primary action, and it is not a circled outline.
	import MessageSquareIcon from '@lucide/svelte/icons/message-square';
	import { Button } from '../../ui/button/index.js';
	import { InputGroup, InputGroupAddon, InputGroupTextarea } from '../../ui/input-group/index.js';

	let {
		value = $bindable(''),
		name,
		placeholder = 'Add a note…',
		rows = 3,
		maxlength = 4000,
		required = false,
		disabled = false,
		pending = false,
		onkeydown,
		onsubmit
	}: {
		value?: string;
		/** Set when the field posts with a surrounding form. */
		name?: string;
		placeholder?: string;
		rows?: number;
		maxlength?: number;
		required?: boolean;
		disabled?: boolean;
		pending?: boolean;
		onkeydown?: (event: KeyboardEvent) => void;
		/** Set when the icon saves itself. Absent, the icon submits the surrounding form. */
		onsubmit?: () => void;
	} = $props();
</script>

<InputGroup class="bg-muted/50">
	<InputGroupTextarea
		bind:value
		{name}
		{placeholder}
		{rows}
		{maxlength}
		{required}
		{disabled}
		{onkeydown}
	/>
	<InputGroupAddon align="block-end" class="justify-end">
		<Button
			type={onsubmit ? 'button' : 'submit'}
			variant="ghost"
			size="icon-sm"
			class="text-foreground"
			disabled={disabled || pending}
			title="Add note"
			aria-label="Add note"
			onclick={onsubmit}
		>
			<MessageSquareIcon class="size-4" />
		</Button>
	</InputGroupAddon>
</InputGroup>
