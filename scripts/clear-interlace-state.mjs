#!/usr/bin/env node
// `api:db-reset` drops the Postgres catalogue and the RustFS volume. The daemons
// started by `moon run :dev` keep their control plane on the host, under
// services/*/.interlace. Leaving those SQLite files makes the next run look up
// snapshot tables the reset just destroyed (`Table with name clients__<hash>
// does not exist`). Compose daemons store the same files on named volumes,
// which `down -v` already removes.
//
// A daemon that is still running holds the old file until it is restarted.
import { rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const services = ['seccl', 'sei', 'morgan-lloyd'];
const names = [
	'state.db',
	'state.db-wal',
	'state.db-shm',
	'streams.db',
	'streams.db-wal',
	'streams.db-shm'
];

for (const service of services) {
	for (const name of names) {
		rmSync(join(root, 'services', service, '.interlace', name), { force: true });
	}
	console.log(`cleared interlace state for ${service}`);
}
