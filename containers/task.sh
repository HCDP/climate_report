#!/bin/bash

set -euo pipefail

echo "[task.sh] [1/5] Starting Execution."
export TZ="HST"
echo "It is currently $(date)."
if [[ -v CUSTOM_DATE ]]; then
    export CUSTOM_DATE=$(date -d "$CUSTOM_DATE" +"%Y-%m-01")
    export CUSTOM_DATE=$(date -d "$CUSTOM_DATE +1 month -1 day" --iso-8601)
    echo "An aggregation date was provided by the environment. Setting to last day of provided month."
else
    export CUSTOM_DATE=$(date -d "-$(date +%d) days" --iso-8601)
    echo "No aggregation date was provided by the environment. Defaulting to last month."
fi

echo "Aggregation date is: " $CUSTOM_DATE
source /workspace/envs/prod.env

# Fill in workflow scripts

cd /sync
echo "[task.sh] [4/5] Uploading data."
python3 inject_upload_config.py config.json $CUSTOM_DATE

echo "[task.sh] [5/5] Uploading data."
python3 upload.py

echo "[task.sh] All done!"