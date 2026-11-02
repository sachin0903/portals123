<script lang="ts">
	import { SetPasswordForm } from '@graphene/ui';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<SetPasswordForm
	token={data.token}
	tokenKind={data.tokenKind}
	{form}
	subtext={data.hint}
	minLength={data.minLength}
/>
