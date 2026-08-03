import os
import urllib3
from urllib3.util import Retry
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dateutil import parser 
import sys
import pytz

     

def check_month():
  hst = pytz.timezone("HST")
  workflow_month_valid = True
  if len(sys.argv) > 1:
    input_date = sys.argv[1]
    workflow_date = parser.parse(input_date).astimezone(hst)
    
    today = datetime.now(hst)
    today = today.replace(hour = 0, minute = 0, second = 0, microsecond = 0)
    run_date = today - relativedelta(months = 1)
    
    if run_date.month != workflow_date.month or run_date.day != workflow_date.day:
      workflow_month_valid = False

  return workflow_month_valid
  


def configure_workflow():
  token = os.getenv("HCDP_API_TOKEN")
  api_base = "https://api.hcdp.ikewai.org"
  ep = "/mesonet/climate_report/configure"
  url = f"{api_base}{ep}"

  headers = {
      "Authorization": f"Bearer {token}"
  }
  retry_config = Retry(
    total = 3, 
    backoff_factor = 2,
    status_forcelist = [408, 425, 429, 500, 502, 503, 504],
    allowed_methods = ["GET", "POST"],
    raise_on_status = True
  )
  
  res = urllib3.request(
    "GET",
    url,
    retries = retry_config,
    headers = headers
  )
  
  if res.status != 200:
    raise Exception(f"Configuration check request failed with status {res.status}: {res.data.decode('utf-8')}")

  is_configured = res.json()["configured"]
  if is_configured:
    print("Workflow already configured. Skipping configuration...")
  else:
    res = urllib3.request(
      "POST",
      url,
      retries = retry_config,
      headers = headers
    )
    if res.status != 204:
      raise Exception(f"Workflow configuration failed with status code{res.status}")




def main():
  if check_month():
    configure_workflow()


if __name__ == "__main__":
  main()