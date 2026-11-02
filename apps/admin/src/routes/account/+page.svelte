<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		Input,
		Label
	} from '@graphene/ui';
	import InfoIcon from '@lucide/svelte/icons/info';

	let { data, form } = $props();
</script>

<div class="space-y-6">
	<!-- Honest by construction: there is no endpoint that persists these, so the page does not offer
	     a Save button it cannot honour. It used to accept the edit and claim "Your details have been
	     saved" while a footnote said changes weren't persisted. -->
	<Alert>
		<InfoIcon class="size-4" />
		<AlertDescription>
			Your name and email address come from your firm’s directory. Ask an administrator to change
			them.
		</AlertDescription>
	</Alert>

	{#if form?.error}
		<Alert variant="destructive">
			<AlertDescription>{form.error}</AlertDescription>
		</Alert>
	{/if}

	<Card glass>
		<CardHeader>
			<CardTitle>Personal details</CardTitle>
			<CardDescription>Your name as it appears on your account.</CardDescription>
		</CardHeader>
		<CardContent class="grid gap-4 sm:grid-cols-2">
			<div class="grid gap-1.5">
				<Label for="firstName">First name</Label>
				<Input id="firstName" name="firstName" value={data.account.firstName ?? ''} readonly />
			</div>
			<div class="grid gap-1.5">
				<Label for="lastName">Last name</Label>
				<Input id="lastName" name="lastName" value={data.account.lastName ?? ''} readonly />
			</div>
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Contact details</CardTitle>
			<CardDescription>How we reach you.</CardDescription>
		</CardHeader>
		<CardContent class="grid gap-4 sm:grid-cols-2">
			<div class="grid gap-1.5">
				<Label for="email">Email address</Label>
				<Input id="email" name="email" type="email" value={data.account.email} readonly />
			</div>
		</CardContent>
	</Card>
</div>
