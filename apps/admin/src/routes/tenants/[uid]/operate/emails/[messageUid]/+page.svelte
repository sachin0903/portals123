<script lang="ts">
	import { MessageDetail } from '@graphene/ui/comms';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<a
	class="text-muted-foreground text-sm underline"
	href={`/tenants/${data.tenantUid}/operate/emails`}>← Emails</a
><MessageDetail
	message={data.message}
	clientHref={(uid) => `/tenants/${data.tenantUid}/operate/clients/${uid}`}
/>
