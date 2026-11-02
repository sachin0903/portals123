<script lang="ts">
	import { ForgotPasswordForm } from '@graphene/ui';
	import type { ActionData } from './$types';

	let { form }: { form: ActionData } = $props();
</script>

<ForgotPasswordForm {form} />
