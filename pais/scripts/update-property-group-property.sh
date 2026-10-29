#!/bin/bash
set -euo pipefail

# Updates a single property's value within an EXISTING VCF Automation property group,
# without touching any of that group's other properties.
#
# Example: property group "paif_dl_vgpu_ai_workshop", property "vgpu_license", new value "X".
#
# VCF Automation 9 no longer supports minting a refresh token from a username/password via
# the API. Generate a personal API (refresh) token once via the VCF Automation UI
# (click your username > My Account > API Tokens > New) and provide it when prompted below -
# this script exchanges it for a short-lived access token, then uses that to call the
# Properties API.

ENV_FILE=".env.vcfa-property-update"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

for cmd in curl jq; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is required but not installed." >&2
        exit 1
    fi
done

# Prompts for a variable if it isn't already present in the env file, then persists it.
# Usage: ensure_env_var VAR_NAME "Prompt text" [silent]
ensure_env_var() {
    local var_name="$1"
    local prompt_text="$2"
    local silent="${3:-}"

    if grep -q "^${var_name}=" "$ENV_FILE"; then
        return
    fi

    echo "$prompt_text"
    local value
    if [ "$silent" = "silent" ]; then
        read -rs value
        echo
    else
        read -r value
    fi
    echo "${var_name}=${value}" >> "$ENV_FILE"
}

# Note: secrets below (including the refresh token) are persisted in plaintext to
# "$ENV_FILE" so re-runs don't re-prompt for everything. Treat this file like a
# credential - keep it out of version control and delete it when you're done.

# 1. VCF Automation connection details
ensure_env_var VCFA_FQDN "Enter the VCF Automation appliance FQDN (e.g. vcfa.example.com):"
ensure_env_var VCFA_ORG "Enter the VCF Automation organization/tenant name:"
ensure_env_var VCFA_REFRESH_TOKEN "Enter your VCF Automation API (refresh) token (generated once via the UI):" silent
ensure_env_var VCFA_INSECURE "Skip TLS certificate verification? Only use this for self-signed/lab certs. (true/false):"

# Load everything collected above
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

CURL_OPTS=(-sS)
case "${VCFA_INSECURE,,}" in
    true|yes|y)
        CURL_OPTS+=(-k)
        ;;
esac

# 2. What to update - asked fresh every run, not persisted (these change per invocation)
echo "Enter the name of the EXISTING property group to update:"
read -r TARGET_GROUP_NAME
echo "Enter the name of the property within '${TARGET_GROUP_NAME}' to set:"
read -r TARGET_PROPERTY_NAME
echo "Enter the new value for '${TARGET_PROPERTY_NAME}':"
read -rs TARGET_PROPERTY_VALUE
echo

# 3. Exchange the refresh token for a short-lived access token
echo "Authenticating to VCF Automation (${VCFA_FQDN})..."
TOKEN_RESPONSE=$(curl "${CURL_OPTS[@]}" -X POST "https://${VCFA_FQDN}/oauth/tenant/${VCFA_ORG}/token" \
    -H "Accept: application/*" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=refresh_token" \
    --data-urlencode "refresh_token=${VCFA_REFRESH_TOKEN}")

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token // empty')
if [ -z "$ACCESS_TOKEN" ]; then
    echo "Error: Failed to obtain an access token. Response was:" >&2
    echo "$TOKEN_RESPONSE" >&2
    exit 1
fi
echo "Authentication successful."

# 4. Find the existing property group by name (the API only supports lookup/update by ID)
echo "Looking up property group '${TARGET_GROUP_NAME}'..."
LIST_RESPONSE_FILE=$(mktemp)
trap 'rm -f "$LIST_RESPONSE_FILE" "${GROUP_RESPONSE_FILE:-}"' EXIT

HTTP_STATUS=$(curl "${CURL_OPTS[@]}" -o "$LIST_RESPONSE_FILE" -w "%{http_code}" \
    -X GET "https://${VCFA_FQDN}/properties/api/property-groups?size=1000" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Accept: application/json")

if [[ ! "$HTTP_STATUS" =~ ^2 ]]; then
    echo "Failure: Failed to list property groups (HTTP $HTTP_STATUS). Response:" >&2
    cat "$LIST_RESPONSE_FILE" >&2
    exit 1
fi

GROUP_ID=$(jq -r --arg name "$TARGET_GROUP_NAME" \
    '(.content? // .)[] | select(.name == $name) | .id' "$LIST_RESPONSE_FILE" | head -n1)

if [ -z "$GROUP_ID" ] || [ "$GROUP_ID" = "null" ]; then
    echo "Error: No property group named '${TARGET_GROUP_NAME}' was found." >&2
    exit 1
fi
echo "Found property group '${TARGET_GROUP_NAME}' (id: ${GROUP_ID})."

# 5. Fetch the full existing property group (read-modify-write, so unrelated properties
#    and group metadata are left untouched)
GROUP_RESPONSE_FILE=$(mktemp)
HTTP_STATUS=$(curl "${CURL_OPTS[@]}" -o "$GROUP_RESPONSE_FILE" -w "%{http_code}" \
    -X GET "https://${VCFA_FQDN}/properties/api/property-groups/${GROUP_ID}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Accept: application/json")

if [[ ! "$HTTP_STATUS" =~ ^2 ]]; then
    echo "Failure: Failed to fetch property group '${TARGET_GROUP_NAME}' (HTTP $HTTP_STATUS). Response:" >&2
    cat "$GROUP_RESPONSE_FILE" >&2
    exit 1
fi

GROUP_TYPE=$(jq -r '.type // "CONSTANT"' "$GROUP_RESPONSE_FILE")
PROPERTY_EXISTS=$(jq -r --arg prop "$TARGET_PROPERTY_NAME" \
    '.properties[$prop] != null' "$GROUP_RESPONSE_FILE")

# CONSTANT groups store their fixed value in "const"; INPUT groups use "default" as the
# pre-filled/suggested value on the request form.
VALUE_FIELD="const"
if [ "$GROUP_TYPE" = "INPUT" ]; then
    VALUE_FIELD="default"
fi

if [ "$PROPERTY_EXISTS" = "true" ]; then
    OLD_VALUE=$(jq -r --arg prop "$TARGET_PROPERTY_NAME" --arg field "$VALUE_FIELD" \
        '.properties[$prop][$field] // "(not set)"' "$GROUP_RESPONSE_FILE")
    echo "Property '${TARGET_PROPERTY_NAME}' exists. Current ${VALUE_FIELD}: ${OLD_VALUE}"
    ENCRYPTED_FLAG=""
else
    echo "Property '${TARGET_PROPERTY_NAME}' does not exist in this group yet - it will be created."
    echo "Should it be marked encrypted? (true/false):"
    read -r NEW_PROP_ENCRYPTED
    ENCRYPTED_FLAG="$NEW_PROP_ENCRYPTED"
fi

echo
echo "About to set '${TARGET_GROUP_NAME}.${TARGET_PROPERTY_NAME}' (${VALUE_FIELD}) to the value you entered."
echo "Proceed? (yes/no):"
read -r CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted - no changes made."
    exit 0
fi

# 6. Build the updated property group body, modifying only the target property
if [ "$PROPERTY_EXISTS" = "true" ]; then
    UPDATED_GROUP=$(jq --arg prop "$TARGET_PROPERTY_NAME" --arg field "$VALUE_FIELD" --arg val "$TARGET_PROPERTY_VALUE" \
        '.properties[$prop][$field] = $val' "$GROUP_RESPONSE_FILE")
else
    UPDATED_GROUP=$(jq --arg prop "$TARGET_PROPERTY_NAME" --arg field "$VALUE_FIELD" --arg val "$TARGET_PROPERTY_VALUE" \
        --argjson encrypted "$([ "${ENCRYPTED_FLAG,,}" = "true" ] && echo true || echo false)" \
        '.properties[$prop] = { type: "string", encrypted: $encrypted } | .properties[$prop][$field] = $val' \
        "$GROUP_RESPONSE_FILE")
fi

# 7. Save it back
echo "Updating property group '${TARGET_GROUP_NAME}'..."
PUT_RESPONSE_FILE=$(mktemp)
trap 'rm -f "$LIST_RESPONSE_FILE" "$GROUP_RESPONSE_FILE" "$PUT_RESPONSE_FILE"' EXIT

HTTP_STATUS=$(curl "${CURL_OPTS[@]}" -o "$PUT_RESPONSE_FILE" -w "%{http_code}" \
    -X PUT "https://${VCFA_FQDN}/properties/api/property-groups/${GROUP_ID}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$UPDATED_GROUP")

if [[ "$HTTP_STATUS" =~ ^2 ]]; then
    echo "Success: '${TARGET_GROUP_NAME}.${TARGET_PROPERTY_NAME}' updated."
else
    echo "Failure: Failed to update property group (HTTP $HTTP_STATUS). Response:" >&2
    cat "$PUT_RESPONSE_FILE" >&2
    exit 1
fi
