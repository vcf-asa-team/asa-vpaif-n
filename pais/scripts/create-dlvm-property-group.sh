#!/bin/bash
set -euo pipefail

# Creates (or re-creates) the "vpaif_dlvm_properties" CONSTANT property group in
# VCF Automation, used by the DL workload blueprints in this repo (propgroup.vpaif_dlvm_properties.*).
#
# VCF Automation 9 no longer supports minting a refresh token from a username/password via
# the API. Generate a personal API (refresh) token once via the VCF Automation UI
# (user preferences / API tokens) and provide it when prompted below - this script exchanges
# it for a short-lived access token, then uses that to call the Properties API.

ENV_FILE=".env.vpaif-dlvm-property-group"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

PROPERTY_GROUP_NAME="vpaif_dlvm_properties"

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

# 2. Property values for the vpaif_dlvm_properties constant property group
ensure_env_var PROP_HARBOR "Enter the value for 'harbor' (Harbor registry hostname):"
ensure_env_var PROP_HF_TOKEN "Enter the value for 'hfToken' (Hugging Face token):" silent
ensure_env_var PROP_NGC_API_KEY "Enter the value for 'NGC_API_KEY':" silent
ensure_env_var PROP_PAIS_API_TOKEN "Enter the value for 'paisApiToken':" silent
ensure_env_var PROP_STORAGE_CLASS "Enter the value for 'storageClass':"
ensure_env_var PROP_VMI_IMAGE_NAME "Enter the value for 'vmiImageName':"

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

# 4. Build the property group payload.
#    type=CONSTANT means these values are fixed and not editable by the requester at deploy time
#    (as opposed to an INPUT property group, which prompts the user on a request form).
PAYLOAD=$(jq -n \
    --arg name "$PROPERTY_GROUP_NAME" \
    --arg harbor "$PROP_HARBOR" \
    --arg hfToken "$PROP_HF_TOKEN" \
    --arg ngcApiKey "$PROP_NGC_API_KEY" \
    --arg paisApiToken "$PROP_PAIS_API_TOKEN" \
    --arg storageClass "$PROP_STORAGE_CLASS" \
    --arg vmiImageName "$PROP_VMI_IMAGE_NAME" \
    '{
        name: $name,
        type: "CONSTANT",
        properties: {
            harbor: { type: "string", const: $harbor },
            hfToken: { type: "string", const: $hfToken, encrypted: true },
            NGC_API_KEY: { type: "string", const: $ngcApiKey, encrypted: true },
            paisApiToken: { type: "string", const: $paisApiToken, encrypted: true },
            storageClass: { type: "string", const: $storageClass },
            vmiImageName: { type: "string", const: $vmiImageName }
        }
    }')

# 5. Create the property group
echo "Creating property group '${PROPERTY_GROUP_NAME}'..."
RESPONSE_FILE=$(mktemp)
trap 'rm -f "$RESPONSE_FILE"' EXIT

HTTP_STATUS=$(curl "${CURL_OPTS[@]}" -o "$RESPONSE_FILE" -w "%{http_code}" \
    -X POST "https://${VCFA_FQDN}/properties/api/property-groups" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

if [[ "$HTTP_STATUS" =~ ^2 ]]; then
    echo "Success: Property group '${PROPERTY_GROUP_NAME}' created."
    jq '.' "$RESPONSE_FILE"
elif [ "$HTTP_STATUS" = "409" ]; then
    echo "Failure: A property group named '${PROPERTY_GROUP_NAME}' already exists (HTTP 409)." >&2
    echo "This script only creates new property groups; use the VCF Automation UI or the PUT" >&2
    echo "/properties/api/property-groups/{propertyGroupId} endpoint to update an existing one." >&2
    cat "$RESPONSE_FILE" >&2
    exit 1
else
    echo "Failure: Failed to create property group (HTTP $HTTP_STATUS). Response:" >&2
    cat "$RESPONSE_FILE" >&2
    exit 1
fi
