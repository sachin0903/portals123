// https://heyapi.dev — generates the typed client from FastAPI's OpenAPI schema.
// Verify options against the installed Hey API version; the config surface evolves.
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@hey-api/openapi-ts";

// moon runs openapi-ts with the cwd set to this package, so a relative
// `OPENAPI_URL` (e.g. the CI `apps/api/openapi.json`) would resolve against
// pkgs/api-client and miss. Resolve filesystem paths against the repo root
// (two levels up from this file); pass http(s) URLs through untouched.
const repoRoot = fileURLToPath(new URL("../../", import.meta.url));

// The DUMPED schema, not a running server. `api:dump-openapi` writes it and `api-client:generate`
// depends on that task, so this default is what actually gets used; CI sets the same path
// explicitly. An http(s) `OPENAPI_URL` still passes through for a one-off, but be aware that Hey
// API takes the client's default base URL and the `ClientOptions['baseUrl']` TYPE from its input —
// generate from a URL and you commit that origin.
function resolveInput(value: string | undefined): string {
  const input = value ?? "apps/api/openapi.json";
  if (/^https?:\/\//.test(input)) return input;
  return resolve(repoRoot, input);
}

export default defineConfig({
  input: resolveInput(process.env.OPENAPI_URL),
  output: {
    path: "src",
    postProcess: ["prettier"],
  },
  // `baseUrl: false` — THE ARTEFACT MUST NOT DEPEND ON WHERE THE SCHEMA CAME FROM.
  //
  // The plugin's default is to bake a default base URL, inferred from the input when the input is
  // a URL. This package is generated two ways: a developer runs `moon run api-client:generate`
  // against a RUNNING API (the flow CLAUDE.md documents), and CI dumps the schema to
  // `apps/api/openapi.json` and generates from the FILE. With the default, those two produce
  // different files — the dev run bakes `http://localhost:8000` into `client.gen.ts` and narrows
  // `ClientOptions['baseUrl']` to that literal, the CI run emits the generic
  // `${string}://${string}` — so `client-drift`, whose whole job is to diff the committed client
  // against a freshly generated one, went red on every branch as soon as somebody regenerated the
  // documented way. Off, both paths agree.
  //
  // Nothing loses a default it was relying on: the portals reach the API through their own
  // `apiUrl()` fetch wrappers and import only `@graphene/api-client/permissions`. A baked
  // `localhost` default would have been wrong for any consumer that ever did use it.
  plugins: [{ name: "@hey-api/client-fetch", baseUrl: false }],
});
