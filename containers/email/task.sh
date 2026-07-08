#!/bin/bash

set -euo pipefail

echo "[task.sh] [1/1] Send emails."
python3 /workspace/code/send_email.py

echo "[task.sh] All done!"