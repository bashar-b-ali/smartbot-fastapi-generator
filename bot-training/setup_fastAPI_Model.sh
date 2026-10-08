#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODEL_NAME="fastAPI_Model"
OFFLINE=0

usage() {
    echo "Usage: $0 [--offline] [--model-name NAME]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --offline) OFFLINE=1; shift ;;
        --model-name) MODEL_NAME=${2:?"--model-name requires a value"}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama is not installed or not available on PATH." >&2
    exit 1
fi

MODELFILE="$SCRIPT_DIR/Modelfile.fastAPI_Model"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11+ is required to download and verify the model." >&2
    exit 1
fi
if [ "$OFFLINE" -eq 1 ]; then
    python3 "$SCRIPT_DIR/download_model.py" --verify-only
else
    python3 "$SCRIPT_DIR/download_model.py"
fi

cd "$SCRIPT_DIR"
ollama create "$MODEL_NAME" -f "$MODELFILE"
ollama show "$MODEL_NAME"
echo "Model is ready: $MODEL_NAME"
