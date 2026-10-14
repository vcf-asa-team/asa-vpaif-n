#!/bin/bash
set -euo pipefail

ENV_FILE=".env"
touch "$ENV_FILE"

# Prompts for a variable if it isn't already present in .env, then persists it.
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

# 1. Check if huggingface_hub[cli] is installed
echo "Checking for huggingface_hub[cli]..."
if ! python3 -c "import huggingface_hub" &> /dev/null; then
    echo "huggingface_hub not found. Installing..."
    python3 -m pip install "huggingface_hub[cli]"
else
    echo "huggingface_hub is already installed."
fi

# huggingface_hub's HTTP/2 downloader (httpx2) calls brotli's
# Decompressor.process() with output_buffer_limit, which only exists in
# brotli>=1.2.0. Older brotli versions cause downloads to fail with
# "TypeError: process() takes no keyword arguments".
echo "Ensuring brotli>=1.2.0 is installed..."
python3 -m pip install --upgrade "brotli>=1.2.0"

# 2. Collect required secrets/config into .env
# Note: In a real scenario, you should use a secret manager instead of a plaintext .env file
ensure_env_var HUGGINGFACE_TOKEN "Please enter your Hugging Face Token:" silent
ensure_env_var HARBOR "Please enter the Harbor registry hostname (e.g. harbor.example.com):"
ensure_env_var MODEL_STORE "Please enter the Harbor model store/project path:"
ensure_env_var HARBOR_USERNAME "Please enter your Harbor username:"
ensure_env_var HARBOR_PASSWORD "Please enter your Harbor password:" silent

# Load variables from .env
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# 3. Log into Hugging Face
echo "Logging into Hugging Face..."
hf auth login --token "$HUGGINGFACE_TOKEN"

# 4. Ask for the Hugging Face model name and create a download directory
echo "Please enter the Hugging Face model name to download:"
read -r MODEL_NAME
MODEL_NAME="${MODEL_NAME,,}"
echo "Using lowercase model name: $MODEL_NAME"
MODEL_DIR="/opt/data/model-store"
MODEL_DOWNLOAD_DIR="$MODEL_DIR/$MODEL_NAME"
echo "Creating directory: $MODEL_DOWNLOAD_DIR"
mkdir -p "$MODEL_DOWNLOAD_DIR"
hf download "$MODEL_NAME" --local-dir "$MODEL_DOWNLOAD_DIR"

# 5. make sure docker ignores self signed certificates
if [ ! -f /etc/docker/daemon.json ]; then
    echo '{}' | sudo tee /etc/docker/daemon.json > /dev/null
fi

if grep -q "$HARBOR" /etc/docker/daemon.json; then
    echo "Harbor is already in the Docker daemon.json."
else
    echo "Adding Harbor to Docker daemon.json..."
    sudo jq --arg harbor "$HARBOR" \
        '.["insecure-registries"] = ((.["insecure-registries"] // []) + [$harbor] | unique)' \
        /etc/docker/daemon.json > /opt/data/temp.json && sudo mv /opt/data/temp.json /etc/docker/daemon.json
    sudo systemctl restart docker
fi

# check to see if the harbor certificate is already in the trusted store
if [ ! -f "/usr/local/share/ca-certificates/${HARBOR}.crt" ]; then
    echo "Adding Harbor certificate to trusted store..."
    sudo openssl s_client -showcerts -servername "$HARBOR" -connect "$HARBOR:443" </dev/null | sed -ne '/-BEGIN CERTIFICATE-/,/-END CERTIFICATE-/p' | sudo tee "/usr/local/share/ca-certificates/${HARBOR}.crt" > /dev/null
    sudo update-ca-certificates
fi

# 6. Log into Harbor and run the push command
echo "Logging into Harbor..."
echo "$HARBOR_PASSWORD" | sudo docker login "$HARBOR" -u "$HARBOR_USERNAME" --password-stdin

cd "$MODEL_DOWNLOAD_DIR"
HARBOR_MODEL_STORE="${HARBOR}/${MODEL_STORE}"
echo "Pushing model to Harbor..."
vcf pais models push --modelName "$MODEL_NAME" --modelStore "$HARBOR_MODEL_STORE" -t v1
