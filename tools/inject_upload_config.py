import json, sys
from datetime import datetime

config_file = sys.argv[1]
date_s = sys.argv[2]

config = None
# Load the config file specified as the first command line argument
with open(config_file, "r") as f:
    config = json.load(f)

inject_date = datetime.fromisoformat(date_s)
config_s = json.dumps(config, indent = 4)
config_s = config_s.replace("%y", inject_date.strftime("%Y"))
config_s = config_s.replace("%m", inject_date.strftime("%m"))
config_s = config_s.replace("%d", inject_date.strftime("%d"))

# write updated config
with open(config_file, "w") as f:
    f.write(config_s)