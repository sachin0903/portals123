#!/usr/bin/env node
// Create the `graphene-dev` docker network if it is missing. The compose file marks it external
// so `down` cannot detach a standalone Postgres; compose will not create it.
//
// NODE, not sh. This was `ensure-graphene-dev-network.sh`, invoked as `sh scripts/…` — and on
// Windows `sh` is not on PATH unless Git's `usr/bin` happens to be, so `moon run :dev` died on the
// first word of `api:db-up` with "The term 'sh' is not recognized". That is every one of this
// repo's docker tasks refusing to start, for a nine-line script that runs two docker commands.
// Node is pinned by proto and every other task in the repo already uses it.
import { spawnSync } from 'node:child_process';

const NETWORK = 'graphene-dev';

const inspect = spawnSync('docker', ['network', 'inspect', NETWORK], { stdio: 'ignore' });
if (inspect.error) {
	// Docker itself is unreachable. Say so here rather than letting the `docker compose` that
	// follows fail with the same cause and a longer message.
	console.error(`Could not run docker: ${inspect.error.message}`);
	process.exit(1);
}
if (inspect.status === 0) process.exit(0);

const created = spawnSync('docker', ['network', 'create', NETWORK], { stdio: 'inherit' });
if (created.status !== 0) process.exit(created.status ?? 1);
console.log(`created docker network ${NETWORK}`);
