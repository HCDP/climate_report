import os
import sys
import pytz
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta
from os.path import join
from dateutil import parser

hcdp_api_token = os.environ.get('HCDP_API_ADMIN_TOKEN')
local_dep_dir = os.environ.get('DEPENDENCY_DIR') 

datasets = [
    ({"datatype": "rainfall", "production": "legacy"}, "rainfall_legacy"),
    ({"datatype": "rainfall", "production": "new"}, "rainfall_new"),
    ({"datatype": "temperature", "aggregation":"mean"}, "temperature"),
    ({"datatype": "spi", "timescale": "timescale003"}, "spi3")
]

def dataset2params(dataset):
    return "&".join("=".join(item) for item in dataset.items())

def get_raster(date_str, dataset_dict, outf):
    """Fetches raster data from HCDP API with built-in retries for timeouts."""
    url = f"https://api.hcdp.ikewai.org/raster?period=month&date={date_str}&extent=statewide&{dataset2params(dataset_dict)}"
    print(url)
    headers = {'Authorization': f'Bearer {hcdp_api_token}'}
    found = False
    res = requests.get(url, headers=headers)
    if res.status_code != 404:
        res.raise_for_status()
        with open(outf, 'wb') as f:
            f.write(res.content)
        found = True
    return found

def fetch_tifs(dataset_prefix, dataset_dict, start_year, end_year, month):
  end_year_found = False
  for year in range(start_year, end_year + 1):
    date_str = f"{year}-{month:02d}"
    filename = f"{dataset_prefix}_{year}_{month:02d}.tif"
    outf = join(local_dep_dir, dataset_prefix, filename)

    print(f"Fetching: {date_str} ({dataset_prefix})")
    success = get_raster(date_str, dataset_dict, outf)
    if year == end_year:
      end_year_found = success
  return end_year_found


if __name__ == "__main__":
  hst = pytz.timezone('HST')
  date = None

  if len(sys.argv) > 1:
      input_date = sys.argv[1]
      date = parser.parse(input_date).astimezone(hst)
  else:
      today = datetime.now(hst)
      today = today.replace(hour=0, minute=0, second=0, microsecond=0)
      date = today - relativedelta(months=1)

  month_value = date.month
  year_value = date.year

  print(f"Target Date: {date.strftime('%Y-%m-%d')}")
  print(f"Fetching all historical data for Month: {month_value:02d}")

  # 1. Fetch Legacy Rainfall (1920 - 1989) for this specific month
  fetch_tifs(
      dataset_prefix="rainfall",
      dataset_dict=datasets[0][0],
      start_year=1920,
      end_year=1989,
      month=month_value
  )

  # 2. Fetch New Rainfall (1990 - target year) for this specific month
  # Fetch YTD rainfall (Jan through month before target) for the target year
  for m in range(1, month_value):
      fetch_tifs(
          dataset_prefix="rainfall",
          dataset_dict=datasets[1][0],
          start_year=year_value,
          end_year=year_value,
          month=m
      )

  rainfall_ok = fetch_tifs(
      dataset_prefix="rainfall",
      dataset_dict=datasets[1][0],
      start_year=1990,
      end_year=year_value,
      month=month_value
  )

  # 3. Fetch Temperature (1990 - target year) for this specific month
  temperature_ok = fetch_tifs(
      dataset_prefix="temperature",
      dataset_dict=datasets[2][0],
      start_year=1990,
      end_year=year_value,
      month=month_value
  )

  fetch_tifs(
      dataset_prefix="spi3",
      dataset_dict=datasets[3][0],
      start_year=year_value,
      end_year=year_value,
      month=month_value
  )

  if not rainfall_ok:
      print(f"ERROR: No 200 response for rainfall {year_value}-{month_value:02d}. Stopping.")
      sys.exit(1)
  if not temperature_ok:
      print(f"ERROR: No 200 response for temperature {year_value}-{month_value:02d}. Stopping.")
      sys.exit(1)

