#!/usr/bin/env bash
# Reproduce `cam_createscheme` with curl alone, outside our client.
#
# WHY IT EXISTS: the create is refused with `403 0x8004f502 ... ML Team secured field on entity
# Scheme` for every valid schemeType/sippType pairing, having worked for this same service
# principal on 30 Jul and 3 Aug 2026 (docs/morgan-lloyd/endpoint-coverage.md §4). This takes our
# code out of the picture entirely, so the refusal can be shown to Morgan Lloyd as a bare HTTP
# exchange — and re-tested in one command once they change a permission.
#
#   ./scripts/try_create_scheme.sh <contactId> [schemeType] [sippType]
#
# Credentials are READ FROM `.env`, never passed on the command line: an argument is visible in
# `ps` to every user on the box, and this one is the client secret. The token request goes over
# stdin for the same reason, and nothing here echoes either value.

set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "no services/morgan-lloyd/.env — nothing to authenticate with" >&2; exit 2; }
set -a; . ./.env; set +a

CONTACT_ID="${1:-}"
SCHEME_TYPE="${2:-${MORGAN_LLOYD_SCHEME_TYPE:-16}}"
SIPP_TYPE="${3:-${MORGAN_LLOYD_SIPP_TYPE:-809130016}}"
RESOURCE="${MORGAN_LLOYD_RESOURCE_URL:?set MORGAN_LLOYD_RESOURCE_URL}"

if [ -z "$CONTACT_ID" ]; then
  echo "usage: $0 <contactId> [schemeType] [sippType]" >&2
  echo "  a contactId is any GUID from GET ${RESOURCE}/api/data/v9.1/contacts" >&2
  exit 2
fi

echo "org         : ${RESOURCE}"
echo "client_id   : ${MORGAN_LLOYD_CLIENT_ID}"
echo "contactId   : ${CONTACT_ID}"
echo "pairing     : schemeType=${SCHEME_TYPE} sippType=${SIPP_TYPE}"
echo

# --- 1. token (Azure AD v1 client-credentials; the resource is the Dynamics org URL) -----------
TOKEN_JSON=$(curl -sS -X POST \
  "https://login.microsoftonline.com/${MORGAN_LLOYD_TENANT_ID}/oauth2/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-binary @- <<EOF
grant_type=client_credentials&client_id=${MORGAN_LLOYD_CLIENT_ID}&client_secret=${MORGAN_LLOYD_CLIENT_SECRET}&resource=${RESOURCE}
EOF
)

TOKEN=$(printf '%s' "$TOKEN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))')
if [ -z "$TOKEN" ]; then
  # The description names the actual problem (AADSTS7000215 invalid secret, AADSTS53003
  # Conditional Access...), where the status code alone does not.
  printf '%s' "$TOKEN_JSON" |
    python3 -c 'import json,sys; b=json.load(sys.stdin); print("token refused:", (b.get("error_description") or b.get("error","")).splitlines()[0])' >&2
  exit 1
fi
echo "token       : acquired"
echo

# --- 2. the create ------------------------------------------------------------------------------
# Exactly the three documented fields. No fee percentages: they are themselves ML-Team secured,
# so sending them would give the create a second, independent reason to fail.
BODY=$(printf '{"contactId":"%s","schemeType":%s,"sippType":%s}' \
  "$CONTACT_ID" "$SCHEME_TYPE" "$SIPP_TYPE")
echo "POST ${RESOURCE}/api/data/v9.1/cam_createscheme"
echo "  ${BODY}"
echo

curl -sS -i -X POST "${RESOURCE}/api/data/v9.1/cam_createscheme" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'OData-MaxVersion: 4.0' \
  -H 'OData-Version: 4.0' \
  -H 'Accept: application/json' \
  -d "$BODY"
echo
