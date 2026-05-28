#!/bin/bash

set -euo pipefail

echo "[task.sh] [1/7] Starting Execution."
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

# Get dependencies (rainfall, temperature and spi3 maps)
echo "[task.sh] [2/7] Get dependencies."
python3 /workspace/code/wget_dependencies.py $CUSTOM_DATE

# Makes the categorical maps and the YTD map
echo "[task.sh] [3/7] Get maps."
python3 /workspace/code/get_maps.py $CUSTOM_DATE

# Calculates all statistics and uploads to database
echo "[task.sh] [4/7] Calculate monthly stats and upload."
python3 /workspace/code/monthly_upload.py $CUSTOM_DATE

echo "[task.sh] [5/7] Send email."
python3 /workspace/code/send_email.py

cd /sync
echo "[task.sh] [6/7] Uploading data."
python3 inject_upload_config.py config.json $CUSTOM_DATE

echo "[task.sh] [7/7] Uploading data."
python3 upload.py


echo "[task.sh] All done!"