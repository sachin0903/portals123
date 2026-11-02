#!/usr/bin/env node
// Serves https on 0.0.0.0 and forwards everything to the plain-http SvelteKit dev server, so a
// phone on the LAN can open the client portal and use its camera for the identity-photo flow —
// phones only allow getUserMedia() on a secure page, and the Vite dev server only serves plain
// http on port 5173 even though it already binds to every interface.
//
// The dev server sends hundreds of unbundled, uncompressed files, which a phone on Wi-Fi fetches
// slowly. So this serves HTTP/2 (many files at once over one connection), compresses text, code
// and WebAssembly on the way (brotli, else gzip; never an event stream), and keeps its own
// connections to the dev server open.
//
// Run `node scripts/phone-https-proxy.mjs` while the client dev server is running, then open one
// of the printed LAN URLs on the phone. Set the API's
// IDENTITY_PHONE_BASE_URL=https://localhost:5443 so the identity-photo link it sends points here
// instead of the plain-http dev server.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { Agent, request as httpRequest } from 'node:http';
import { createSecureServer } from 'node:http2';
import { connect as netConnect } from 'node:net';
import { networkInterfaces } from 'node:os';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { constants as zlibConstants, createBrotliCompress, createGzip } from 'node:zlib';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const cacheDir = join(root, 'node_modules', '.cache', 'phone-https');
const keyPath = join(cacheDir, 'key.pem');
const certPath = join(cacheDir, 'cert.pem');
const sansPath = join(cacheDir, 'sans.json');

const PORT = Number(process.env.PHONE_HTTPS_PORT || 5443);
const TARGET = process.env.PHONE_HTTPS_TARGET || 'http://127.0.0.1:5173';
const target = new URL(TARGET);
const targetPort = target.port ? Number(target.port) : 80;

// One pool of open connections to the dev server, so each of the page's many files does not
// open its own.
const upstream = new Agent({ keepAlive: true, maxSockets: 64 });

// Headers that belong to one connection, not to the request or response: never passed on, and
// HTTP/2 refuses them outright.
const HOP_BY_HOP = new Set([
	'connection',
	'keep-alive',
	'proxy-connection',
	'transfer-encoding',
	'upgrade',
	'http2-settings',
	'te'
]);

// What is worth compressing: text, code, JSON, SVG and WebAssembly. Not an event stream, which
// must reach the page as it is written.
const COMPRESSIBLE =
	/^(text\/(?!event-stream)|application\/(javascript|json|manifest\+json|xml|wasm)|image\/svg\+xml)/;

// Every non-internal IPv4 address on this machine. That is the LAN-reachable set a phone could
// use, so it is also the set the certificate's subjectAltName must cover.
function getLanIPv4Addresses() {
	const addresses = [];
	for (const entries of Object.values(networkInterfaces())) {
		if (!entries) continue;
		for (const entry of entries) {
			const isIPv4 = entry.family === 'IPv4' || entry.family === 4;
			if (isIPv4 && !entry.internal) addresses.push(entry.address);
		}
	}
	return addresses;
}

function buildSanList(lanIps) {
	return [...new Set(['DNS:localhost', 'IP:127.0.0.1', ...lanIps.map((ip) => `IP:${ip}`)])];
}

// openssl ships with Git for Windows. Try PATH first (Git Bash puts it there), then the usual
// Git-for-Windows install path, since a plain Windows shell does not have either on PATH.
function findOpenssl() {
	const candidates = ['openssl', 'C:/Program Files/Git/mingw64/bin/openssl.exe'];
	for (const candidate of candidates) {
		if (!spawnSync(candidate, ['version'], { stdio: 'ignore' }).error) return candidate;
	}
	return null;
}

function readStoredSans() {
	try {
		return JSON.parse(readFileSync(sansPath, 'utf8'));
	} catch {
		return null;
	}
}

// Reuse the cached cert as long as it exists and still covers the current LAN address set.
// Regenerate (new key too) whenever that set has changed, e.g. a different Wi-Fi network.
function ensureCertificate(sanList) {
	const certsExist = existsSync(keyPath) && existsSync(certPath);
	const stored = readStoredSans();
	if (certsExist && stored && JSON.stringify(stored) === JSON.stringify(sanList)) return;

	const opensslCmd = findOpenssl();
	if (!opensslCmd) {
		console.error('openssl not found on PATH — run this script from Git Bash and try again.');
		process.exit(1);
	}

	mkdirSync(cacheDir, { recursive: true });
	const result = spawnSync(
		opensslCmd,
		[
			'req',
			'-x509',
			'-newkey',
			'rsa:2048',
			'-nodes',
			'-keyout',
			keyPath,
			'-out',
			certPath,
			'-days',
			'825',
			'-subj',
			'/CN=localhost',
			'-addext',
			`subjectAltName=${sanList.join(',')}`
		],
		{ stdio: 'inherit' }
	);
	if (result.status !== 0) {
		console.error('openssl failed to generate the self-signed certificate.');
		process.exit(result.status ?? 1);
	}
	writeFileSync(sansPath, JSON.stringify(sanList));
	console.log(`generated self-signed certificate for: ${sanList.join(', ')}`);
}

// SvelteKit checks the Origin header against the http dev server's own URL, so an https Origin
// coming through this proxy fails that check and the form POST is rejected. Rewrite only the
// scheme on an origin/referer that points at the same host the browser thinks it is talking to —
// any other origin is left alone.
function rewriteForwardHeaders(headers, hostHeader) {
	if (!hostHeader) return;
	const httpsPrefix = `https://${hostHeader}`;
	if (headers.origin === httpsPrefix) headers.origin = `http://${hostHeader}`;
	const referer = headers.referer;
	const refererMatches =
		typeof referer === 'string' && (referer === httpsPrefix || referer.startsWith(`${httpsPrefix}/`));
	if (refererMatches) {
		headers.referer = `http://${referer.slice('https://'.length)}`;
	}
}

function rewriteResponseHeaders(headers, hostHeader) {
	if (!hostHeader) return;
	const httpPrefix = `http://${hostHeader}`;
	const location = headers.location;
	if (typeof location === 'string' && location.startsWith(httpPrefix)) {
		headers.location = `https://${location.slice('http://'.length)}`;
	}
}

// The request's headers for the dev server: HTTP/2's pseudo-headers dropped (its `:authority` is
// the Host), and nothing that belongs to the connection.
function forwardHeaders(req) {
	const host = req.headers[':authority'] ?? req.headers.host;
	const headers = {};
	for (const [name, value] of Object.entries(req.headers)) {
		if (!name.startsWith(':') && !HOP_BY_HOP.has(name)) headers[name] = value;
	}
	if (host) headers.host = host;
	rewriteForwardHeaders(headers, host);
	return { headers, host };
}

// The encoding to send a response in: brotli or gzip when the browser takes it, the body is worth
// compressing and the dev server has not compressed it already. Null sends it as it is.
function encodingFor(req, proxyRes) {
	if (req.method === 'HEAD' || proxyRes.statusCode !== 200) return null;
	if (proxyRes.headers['content-encoding']) return null;
	if (!COMPRESSIBLE.test(String(proxyRes.headers['content-type'] ?? ''))) return null;
	const accepted = String(req.headers['accept-encoding'] ?? '');
	if (/\bbr\b/.test(accepted)) return 'br';
	if (/\bgzip\b/.test(accepted)) return 'gzip';
	return null;
}

function proxyRequest(req, res) {
	const { headers, host } = forwardHeaders(req);

	const proxyReq = httpRequest(
		{
			hostname: target.hostname,
			port: targetPort,
			path: req.url,
			method: req.method,
			headers,
			agent: upstream
		},
		(proxyRes) => {
			const resHeaders = {};
			for (const [name, value] of Object.entries(proxyRes.headers)) {
				if (!HOP_BY_HOP.has(name)) resHeaders[name] = value;
			}
			rewriteResponseHeaders(resHeaders, host);
			const encoding = encodingFor(req, proxyRes);
			if (!encoding) {
				res.writeHead(proxyRes.statusCode, resHeaders);
				proxyRes.pipe(res);
				return;
			}
			delete resHeaders['content-length'];
			resHeaders['content-encoding'] = encoding;
			resHeaders.vary = resHeaders.vary ? `${resHeaders.vary}, Accept-Encoding` : 'Accept-Encoding';
			res.writeHead(proxyRes.statusCode, resHeaders);
			// Quality 5, not brotli's slow best: this compresses on every request.
			const compress =
				encoding === 'br'
					? createBrotliCompress({ params: { [zlibConstants.BROTLI_PARAM_QUALITY]: 5 } })
					: createGzip({ level: 6 });
			proxyRes.pipe(compress).pipe(res);
		}
	);
	proxyReq.on('error', (err) => {
		if (!res.headersSent) res.writeHead(502, { 'content-type': 'text/plain' });
		res.end(`phone-https-proxy: ${err.message}`);
	});
	req.pipe(proxyReq);
}

// Raw TCP forwarding for HTTP/1.1 upgrade requests (WebSocket / Vite HMR). The target is always
// plain http, so no TLS here. HMR is not needed on the phone.
function proxyUpgrade(req, socket, head) {
	const targetSocket = netConnect(targetPort, target.hostname, () => {
		const hostHeader = req.headers.host;
		const httpsPrefix = `https://${hostHeader}`;
		let requestLines = `${req.method} ${req.url} HTTP/1.1\r\n`;
		for (let i = 0; i < req.rawHeaders.length; i += 2) {
			const name = req.rawHeaders[i];
			let value = req.rawHeaders[i + 1];
			const lower = name.toLowerCase();
			if (lower === 'origin' && value === httpsPrefix) value = `http://${hostHeader}`;
			else if (lower === 'referer' && value.startsWith(httpsPrefix))
				value = `http://${value.slice('https://'.length)}`;
			requestLines += `${name}: ${value}\r\n`;
		}
		requestLines += '\r\n';
		targetSocket.write(requestLines);
		if (head && head.length) targetSocket.write(head);
		targetSocket.pipe(socket);
		socket.pipe(targetSocket);
	});
	targetSocket.on('error', () => socket.destroy());
	socket.on('error', () => targetSocket.destroy());
}

const lanIps = getLanIPv4Addresses();
ensureCertificate(buildSanList(lanIps));

// HTTP/2 for a browser that speaks it (every current phone does), HTTP/1.1 for anything else.
const server = createSecureServer(
	{ key: readFileSync(keyPath), cert: readFileSync(certPath), allowHTTP1: true },
	proxyRequest
);
server.on('upgrade', proxyUpgrade);
server.listen(PORT, '0.0.0.0', () => {
	console.log(`phone-https-proxy: forwarding https://0.0.0.0:${PORT} -> ${TARGET}`);
	for (const ip of lanIps) console.log(`  https://${ip}:${PORT}`);
	console.log('the phone will warn the certificate is not trusted — choose to continue anyway.');
});
