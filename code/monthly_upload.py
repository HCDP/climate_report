import os
import glob
import json
import requests
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterstats import zonal_stats
import warnings
import argparse
import sys
from datetime import datetime
from dateutil import parser
from dateutil.relativedelta import relativedelta
import pytz

warnings.filterwarnings('ignore')

hcdp_api_token = os.environ.get('HCDP_API_TOKEN')
local_dep_dir = os.environ.get('DEPENDENCY_DIR') 
local_output_dir = os.environ.get('OUTPUT_DIR') 

HEADERS = {
    "Authorization": f"Bearer {hcdp_api_token}",
    "Content-Type": "application/json; charset=utf-8"
}

DROUGHT_API_URL = "https://api.hcdp.ikewai.org/mesonet/climate_report/drought_stats"
DROUGHT_TIF_DIR = local_output_dir
CAT_MAP = {
    0: "D4", 1: "D3", 2: "D2", 3: "D1", 4: "D0",
    5: "Near Normal",
    6: "W0", 7: "W1", 8: "W2", 9: "W3", 10: "W4"
}
DROUGHT_COLS = [
    "island", "division_type", "name", "date",
    "D4", "D3", "D2", "D1", "D0", "Near Normal",
    "W0", "W1", "W2", "W3", "W4"
]

# ---------------------------------------------------------
# 1. HELPER FUNCTIONS
# ---------------------------------------------------------
CHUNK_SIZE = 500

ISLAND_OKINA_MAP = {
    "Hawaii": "Hawaiʻi",   "Hawai'i": "Hawaiʻi",
    "Oahu":   "Oʻahu",     "O'ahu":   "Oʻahu",
    "Kauai":  "Kauaʻi",    "Kaua'i":  "Kauaʻi",
    "Molokai":"Molokaʻi",  "Moloka'i":"Molokaʻi",
    "Lanai":  "Lānaʻi",    "Lana'i":  "Lānaʻi",
    "Niihau": "Niʻihau",   "Ni'ihau": "Niʻihau",
    "Kahoolawe": "Kahoʻolawe", "Kaho'olawe": "Kahoʻolawe",
}

def fetch_existing_ytd(division_type, target_month):
    """Fetch existing ytd_pnormal from the API for all records of a given division_type and month.
    Returns a dict keyed by (island, name, date_str) -> ytd_pnormal."""
    url = "https://api.hcdp.ikewai.org/mesonet/climate_report/rainfall_stats"
    params = {"division_type": division_type}
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=60)
        records = response.json()
    except Exception as e:
        print(f"Warning: could not fetch existing ytd values: {e}")
        return {}

    lookup = {}
    for r in records:
        date_raw = r.get("date", "")
        # API returns "1920-05-01T00:00:00.000Z", normalise to "1920-05-01"
        date_str = date_raw[:10] if date_raw else ""
        if not date_str or int(date_str[5:7]) != target_month:
            continue
        key = (r.get("island"), r.get("name"), date_str)
        ytd = r.get("ytd_pnormal")
        lookup[key] = int(ytd) if ytd is not None else None
    return lookup


def upload_chunks(url, records, use_json=True, timeout=60):
    """POST records in chunks. Returns (statuses, all_ok)."""
    statuses = []
    for i in range(0, len(records), CHUNK_SIZE):
        chunk = records[i:i + CHUNK_SIZE]
        payload = {"overwrite": True, "data": chunk}
        for attempt in range(3):
            try:
                if use_json:
                    response = requests.post(url, json=payload, headers=HEADERS, timeout=timeout)
                else:
                    response = requests.post(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers=HEADERS, timeout=timeout)
                statuses.append(response.status_code)
                break
            except requests.exceptions.Timeout:
                if attempt == 2:
                    statuses.append("Timeout")
            except Exception as e:
                statuses.append(f"Error: {e}")
                break
    all_ok = all(s == 200 for s in statuses)
    return statuses, all_ok

def convert_units(value, dataset_type):
    if value is None or np.isnan(value):
        return np.nan
    if dataset_type == "rainfall":
        return value / 25.4
    elif dataset_type == "temperature":
        return (value * 9/5) + 32
    return value


# ---------------------------------------------------------
# 2. PRE-PROCESSING
# ---------------------------------------------------------
def load_and_prep_shapefile(division):
    print(f"Preparing shapefile for {division}...")
    shapefile = os.path.join(local_dep_dir, "shapefiles", f"{division}.shp")
    gdf = gpd.read_file(shapefile, encoding='utf-8').copy()

    island_col = next((c for c in gdf.columns if c.lower() in ["island", "mokupuni", "isle", "islandname"]), None)
    name_col = next((c for c in gdf.columns if c.lower() in ["name", "division", "moku", "climate_div", "ahupuaa", "county", "name_hwn"]), None)

    okina_regex = u"['‘’`ʻʼ＇]"
    if island_col:
        # Apply explicit island name map first (handles macrons like Lānaʻi), then catch any remaining rogue apostrophes
        gdf[island_col] = gdf[island_col].replace(ISLAND_OKINA_MAP).astype(str).str.replace(okina_regex, "ʻ", regex=True)
    if name_col:
        gdf[name_col] = gdf[name_col].replace(ISLAND_OKINA_MAP).astype(str).str.replace(okina_regex, "ʻ", regex=True)

    gdf['geometry'] = gdf['geometry'].simplify(tolerance=0.001, preserve_topology=True)

    if island_col and name_col:
        is_same_island_dup = gdf.duplicated(subset=[island_col, name_col], keep=False)
        cum_count = gdf.groupby([island_col, name_col]).cumcount() + 1
        gdf.loc[is_same_island_dup, name_col] = (
            gdf.loc[is_same_island_dup, name_col].astype(str) + " " + cum_count[is_same_island_dup].astype(str)
        )
        gdf = gdf.dissolve(by=[island_col, name_col], as_index=False)
        gdf["island_clean"] = gdf[island_col]
        gdf["name_clean"] = gdf[name_col]
    elif name_col:
        gdf = gdf.dissolve(by=name_col, as_index=False)
        gdf["name_clean"] = gdf[name_col]
        gdf["island_clean"] = gdf[name_col] if division == "island" else "Statewide"
    print(gdf[gdf.geometry.isna() | gdf.geometry.is_empty])
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
    
    gdf["division_type"] = division
    return gdf

def precalculate_climatology(gdf, dataset_type, target_month, is_statewide=False):
    climo_cache, climo_ytd_cache = {}, {}
    climo_file = os.path.join(local_dep_dir,"climo", f"{dataset_type}", f"{dataset_type}_1991-2020_{target_month:02d}.tif")

    if is_statewide:
        with rasterio.open(climo_file) as src:
            arr = src.read(1).astype(float)
            arr = np.where(arr == src.nodata, np.nan, arr)
            with np.errstate(all='ignore'):
                climo_cache[target_month] = convert_units(np.nanmean(arr), dataset_type)
        
        if dataset_type == "rainfall":
            climo_ytd_path = os.path.join(local_dep_dir, "climo", "rainfall_ytd", f"YTD_rain_month_{target_month:02d}.tif")
            if os.path.exists(climo_ytd_path):
                with rasterio.open(climo_ytd_path) as src:
                    ytd_arr = src.read(1).astype(float)
                    ytd_arr = np.where(ytd_arr == -9999, np.nan, ytd_arr)
                    with np.errstate(all='ignore'):
                        climo_ytd_cache[target_month] = np.nanmean(ytd_arr)
            else:
                climo_ytd_cache[target_month] = np.nan
                
        return climo_cache, climo_ytd_cache, None

    with rasterio.open(climo_file) as src:
        raster_crs = src.crs

    if gdf.crs != raster_crs:
        print(f"CRS mismatch detected. Reprojecting vector data from {gdf.crs} to {raster_crs}...")
        gdf = gdf.to_crs(raster_crs)
    
    if os.path.exists(climo_file):
        gdf = gdf[gdf.geometry.notna()]
        gdf = gdf[~gdf.geometry.is_empty]
        gdf = gdf.reset_index(drop=True)
        climo_zs = zonal_stats(vectors=gdf, raster=climo_file, stats=["mean"], nodata=None)
        climo_cache[target_month] = [convert_units(c["mean"], dataset_type) if c["mean"] is not None else np.nan for c in climo_zs]
    else:
        climo_cache[target_month] = [np.nan] * len(gdf)
        
    if dataset_type == "rainfall":
        climo_ytd_path = os.path.join(local_dep_dir, "climo", "rainfall_ytd", f"YTD_rain_month_{target_month:02d}.tif")
        if os.path.exists(climo_ytd_path):
            ytd_zs = zonal_stats(vectors=gdf, raster=climo_ytd_path, stats=["mean"], nodata=-9999)
            climo_ytd_cache[target_month] = [c['mean'] if c['mean'] is not None else np.nan for c in ytd_zs]
        else:
            climo_ytd_cache[target_month] = [np.nan] * len(gdf)
                
    return climo_cache, climo_ytd_cache, gdf

# ---------------------------------------------------------
# 3. CORE PROCESSING LOGIC
# ---------------------------------------------------------
def process_and_upload_last_month(target_year, target_month, gdf, climo_cache, climo_ytd_cache, dataset_type, is_statewide=False):
    """Processes history to get the rank, but only uploads the single target year/month."""
    print(f"Loading historical rasters for Month {target_month:02d} to calculate rank...")
    
    all_records = []
    if not is_statewide:
        gdf["climo_mean"] = climo_cache[target_month]

    tif_path = os.path.join(local_dep_dir, "rainfall") if dataset_type == "rainfall" else os.path.join(local_dep_dir, "temperature")
    var = "rainfall" if dataset_type == "rainfall" else "temperature"
    stats_to_compute = ["mean", "max"] if dataset_type == "temperature" else ["mean"]

    # 1. READ ONCE: Calculate stats for EVERY historical year for this specific month
    for tif in sorted(glob.glob(os.path.join(tif_path, f"{var}_*_{target_month:02d}.tif"))):
        parts = os.path.basename(tif).replace(".tif", "").split("_")
        curr_year, curr_month = int(parts[1]), parts[2]
        curr_date = f"{curr_year}-{curr_month}-01"

        # --- STATEWIDE BRANCH ---
        if is_statewide:
            with rasterio.open(tif) as src:
                arr = src.read(1).astype(float)
                arr = np.where(arr == src.nodata, np.nan, arr)
                
                with np.errstate(all='ignore'):
                    if np.isnan(arr).all():
                        mean_raw, max_raw = np.nan, np.nan
                    else:
                        mean_raw = np.nanmean(arr)
                        max_raw = np.nanmax(arr) if dataset_type == "temperature" else np.nan

            mean_val = convert_units(mean_raw, dataset_type) if not np.isnan(mean_raw) else np.nan
            climo_mean = climo_cache[target_month]
            
            anomaly = mean_val - climo_mean if not np.isnan(mean_val) and not np.isnan(climo_mean) else np.nan
            pchange = ((mean_val - climo_mean) / climo_mean) * 100 if dataset_type == "rainfall" and not np.isnan(anomaly) else anomaly
            
            record = {
                "island": "Statewide",
                "division_type": "Statewide",
                "name": "Statewide",
                "date": curr_date,
                "year": curr_year,
                "mean": round(mean_val, 2) if not np.isnan(mean_val) else None,
                "anomaly": round(anomaly, 2) if not np.isnan(anomaly) else None,
                "pchange": round(pchange, 2) if not np.isnan(pchange) else None,
            }
            if dataset_type == "temperature":
                record["max"] = round(convert_units(max_raw, dataset_type), 2) if not np.isnan(max_raw) else None
                
            all_records.append(record)
            
        # --- SHAPEFILE BRANCH ---
        else:
            stats = zonal_stats(vectors=gdf, raster=tif, stats=stats_to_compute, nodata=None)

            for idx, row in gdf.iterrows():
                mean_raw = stats[idx]["mean"]  # safe: gdf index is reset_index'd before this loop
                if mean_raw is None or np.isnan(mean_raw):
                    mean_val, anomaly, pchange = np.nan, np.nan, np.nan
                    if dataset_type == "temperature":
                        max_val = np.nan
                else:
                    mean_val = convert_units(mean_raw, dataset_type)
                    climo_mean = row["climo_mean"]
                    if np.isnan(climo_mean):
                        anomaly, pchange = np.nan, np.nan
                    else:
                        anomaly = mean_val - climo_mean
                        pchange = ((mean_val - climo_mean) / climo_mean) * 100 if dataset_type == "rainfall" else anomaly

                record = {
                    "island": row["island_clean"],
                    "division_type": row["division_type"],
                    "name": row["name_clean"],
                    "date": curr_date,
                    "year": curr_year,
                    "mean": round(mean_val, 1) if not np.isnan(mean_val) else None,
                    "anomaly": round(anomaly, 1) if not np.isnan(anomaly) else None,
                    "pchange": round(pchange, 1) if not np.isnan(pchange) else None,
                }
                if dataset_type == "temperature":
                    max_raw = stats[idx].get("max")
                    record["max"] = int(round(convert_units(max_raw, dataset_type), 0)) if max_raw is not None else None
                    
                all_records.append(record)

    # 2. RANK ONCE
    df = pd.DataFrame(all_records)
    if df.empty:
        return f"Month {target_month:02d}: No historical data found.", False

    df["rank"] = df.groupby(["island", "name"])["anomaly"].rank(method="min", ascending=False)

    # 3. UPLOAD ALL YEARS (ranks change for every year when a new one is added)
    url = f"https://api.hcdp.ikewai.org/mesonet/climate_report/{dataset_type}_stats"
    upload_status = []

    if target_year not in df["year"].values:
        return f"Month {target_month:02d}, Year {target_year}: Missing TIF file. Target raster not found.", False

    # YTD: compute for target year only — prior years don't have all monthly TIFs available
    if dataset_type == "rainfall":
        df["ytd_pnormal"] = None
        current_ytd_path = os.path.join(local_dep_dir, "YTD", f'YTD_{target_year}_{target_month:02d}.tif')
        if not os.path.exists(current_ytd_path):
            print(f"Warning: YTD TIF not found at {current_ytd_path}. Run get_maps.py first.")
            current_ytd_path = None
        if current_ytd_path:
            target_mask = df["year"] == target_year
            if is_statewide:
                with rasterio.open(current_ytd_path) as src:
                    curr_arr = src.read(1).astype(float)
                    curr_arr = np.where(curr_arr == -9999, np.nan, curr_arr)
                    with np.errstate(all='ignore'):
                        curr_mean = np.nanmean(curr_arr)
                climo_ytd_mean = climo_ytd_cache[target_month]
                ytd_val = int(round((curr_mean / climo_ytd_mean) * 100, 0)) if not np.isnan(curr_mean) and not np.isnan(climo_ytd_mean) and climo_ytd_mean != 0 else None
                df.loc[target_mask, "ytd_pnormal"] = ytd_val
            else:
                current_ytd_zs = zonal_stats(vectors=gdf, raster=current_ytd_path, stats=["mean"], nodata=-9999)
                ytd_pnormals = []
                for idx, curr in enumerate(current_ytd_zs):
                    curr_mean = curr['mean']
                    climo_ytd_mean = climo_ytd_cache[target_month][idx]
                    if curr_mean is not None and not np.isnan(climo_ytd_mean) and climo_ytd_mean != 0:
                        ytd_pnormals.append(int(round((curr_mean / climo_ytd_mean) * 100, 0)))
                    else:
                        ytd_pnormals.append(None)
                for i, row_idx in enumerate(df.index[target_mask].tolist()):
                    df.loc[row_idx, "ytd_pnormal"] = ytd_pnormals[i] if i < len(ytd_pnormals) else None

    # Build payload
    base_cols = ["island", "division_type", "name", "date", "mean", "anomaly", "pchange", "rank"]
    if dataset_type == "temperature":
        base_cols.append("max")

    def to_row(row, cols):
        vals = []
        for col, x in zip(cols, row[cols]):
            if isinstance(x, float) and np.isnan(x):
                vals.append(None)
            elif col == "rank" and x is not None:
                vals.append(int(x))
            else:
                vals.append(x.item() if hasattr(x, 'item') else x)
        return vals

    if dataset_type == "rainfall":
        ytd_cols = base_cols + ["ytd_pnormal"]

        # Fetch existing ytd_pnormal for historical years so we don't clobber backfilled values
        division_type = df["division_type"].iloc[0]
        print(f"Fetching existing ytd_pnormal for {division_type}...")
        ytd_lookup = fetch_existing_ytd(division_type, target_month)
        if not ytd_lookup:
            print(f"Warning: ytd_lookup is empty — skipping historical upload to avoid overwriting backfilled ytd_pnormal values.")
            hist_data = []

        hist_mask = df["year"] != target_year

        def hist_row(row):
            key = (row["island"], row["name"], row["date"])
            row = row.copy()
            row["ytd_pnormal"] = ytd_lookup.get(key, None)
            return to_row(row, ytd_cols)

        hist_data   = [hist_row(row) for _, row in df[hist_mask].iterrows()]
        target_data = [to_row(row, ytd_cols) for _, row in df[~hist_mask].iterrows()]

        n_hist   = max(1, (len(hist_data)   + CHUNK_SIZE - 1) // CHUNK_SIZE)
        n_target = max(1, (len(target_data) + CHUNK_SIZE - 1) // CHUNK_SIZE)
        print(f"Uploading {len(hist_data)} historical records in {n_hist} chunk(s)...")
        statuses_hist, ok_hist = upload_chunks(url, hist_data)
        print(f"Uploading {len(target_data)} target-year records in {n_target} chunk(s)...")
        print(f"  Sample target record: {target_data[0] if target_data else 'EMPTY'}")
        statuses_target, ok_target = upload_chunks(url, target_data)
        statuses, all_ok = statuses_hist + statuses_target, ok_hist and ok_target
    else:
        final_data = [to_row(row, base_cols) for _, row in df.iterrows()]
        n_chunks = max(1, (len(final_data) + CHUNK_SIZE - 1) // CHUNK_SIZE)
        print(f"Uploading {len(final_data)} records for Month {target_month:02d} in {n_chunks} chunk(s)...")
        statuses, all_ok = upload_chunks(url, final_data)

    # Upload target month to {dataset_type}_historical: [island, division_type, name, date, value]
    historical_url = f"https://api.hcdp.ikewai.org/mesonet/climate_report/{dataset_type}_historical"
    target_df = df[df["year"] == target_year]
    historical_data = [
        [row["island"], row["division_type"], row["name"], row["date"],
         None if (isinstance(row["mean"], float) and np.isnan(row["mean"])) else row["mean"]]
        for _, row in target_df.iterrows()
    ]
    print(f"Uploading {len(historical_data)} records to {dataset_type}_historical...")
    statuses_h, ok_h = upload_chunks(historical_url, historical_data)
    statuses, all_ok = statuses + statuses_h, all_ok and ok_h

    upload_status.append(f"Month {target_month:02d}: {statuses}")
    return "\n".join(upload_status), all_ok

# ---------------------------------------------------------
# 4. DROUGHT PROCESSING
# ---------------------------------------------------------
def process_drought_month(year, month, division, gdf):
    tif_path = os.path.join(DROUGHT_TIF_DIR, f"spi3_cat_{year}_{month:02d}.tif")
    if not os.path.exists(tif_path):
        return f"Error: TIF not found at {tif_path}", False

    date_str = f"{year}-{month:02d}"
    print(f"Processing drought data for {date_str}...")
    records = []

    with rasterio.open(tif_path) as src:
        nodata = src.nodata

        if division.lower() == "statewide":
            data = src.read(1)
            valid_data = data[data != nodata]
            total_pixels = valid_data.size
            record = {"island": "Statewide", "division_type": "Statewide", "name": "Statewide", "date": date_str}
            if total_pixels > 0:
                unique, counts = np.unique(valid_data, return_counts=True)
                counts_dict = dict(zip(unique, counts))
                for val, code in CAT_MAP.items():
                    record[code] = round((counts_dict.get(val, 0) / total_pixels) * 100, 2)
            else:
                for code in CAT_MAP.values():
                    record[code] = None
            records.append([record.get(col).item() if hasattr(record.get(col), 'item') else record.get(col) for col in DROUGHT_COLS])

        else:
            stats = zonal_stats(vectors=gdf, raster=tif_path, categorical=True, nodata=nodata)
            for idx, row in gdf.iterrows():
                poly_stats = stats[idx]
                clean_stats = {int(k): v for k, v in poly_stats.items() if k is not None} if poly_stats else {}
                total_pixels = sum(clean_stats.values())
                record = {
                    "island": row.get("island_clean", "Statewide"),
                    "division_type": division,
                    "name": row.get("name_clean", "Unknown"),
                    "date": date_str
                }
                if total_pixels > 0:
                    for val, code in CAT_MAP.items():
                        record[code] = round((clean_stats.get(val, 0) / total_pixels) * 100, 2)
                else:
                    for code in CAT_MAP.values():
                        record[code] = None
                records.append([record.get(col).item() if hasattr(record.get(col), 'item') else record.get(col) for col in DROUGHT_COLS])

    if not records:
        return "No records processed.", False

    n_chunks = max(1, (len(records) + CHUNK_SIZE - 1) // CHUNK_SIZE)
    print(f"Uploading {len(records)} drought records in {n_chunks} chunk(s)...")
    statuses, all_ok = upload_chunks(DROUGHT_API_URL, records, use_json=False)
    return f"Drought {date_str}: {statuses}", all_ok

# ---------------------------------------------------------
# 5. MAIN EXECUTION
# ---------------------------------------------------------
ALL_DATASETS  = ['rainfall', 'temperature', 'drought']
ALL_DIVISIONS = ['statewide', 'island', 'climate', 'moku', 'ahupuaa', 'watershed']

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description="Process and upload climate data.")
    arg_parser.add_argument(
        '--dataset',
        type=str,
        default=None,
        choices=['rainfall', 'temperature', 'drought'],
        help="Dataset to process. Defaults to all datasets."
    )
    arg_parser.add_argument(
        '--division',
        type=str,
        default=None,
        help="Spatial division. Defaults to all divisions."
    )
    arg_parser.add_argument(
        '--date',
        type=str,
        default=None,
        help="Target date in ISO 8601 format (e.g., '2026-03'). Defaults to last month."
    )

    args = arg_parser.parse_args()

    datasets_to_run  = [args.dataset] if args.dataset else ALL_DATASETS
    divisions_to_run = [args.division.strip().lower()] if args.division else ALL_DIVISIONS

    if args.date:
        parsed_date  = parser.parse(args.date)
        TARGET_YEAR  = parsed_date.year
        TARGET_MONTH = parsed_date.month
    else:
        hst = pytz.timezone('HST')
        today = datetime.now(hst).replace(hour=0, minute=0, second=0, microsecond=0)
        target_date  = today - relativedelta(months=1)
        TARGET_YEAR  = target_date.year
        TARGET_MONTH = target_date.month

    print(f"Starting run for {TARGET_YEAR}-{TARGET_MONTH:02d}")
    print(f"Datasets: {datasets_to_run} | Divisions: {divisions_to_run}")

    any_failed = False

    for division in divisions_to_run:
        is_statewide = (division == "statewide")
        base_gdf = None if is_statewide else load_and_prep_shapefile(division)

        for dataset in datasets_to_run:
            print(f"\n--- {dataset.upper()} / {division.upper()} ---")

            if dataset == 'drought':
                result, ok = process_drought_month(TARGET_YEAR, TARGET_MONTH, division, base_gdf)
            else:
                climo, climo_ytd, gdf = precalculate_climatology(
                    gdf=base_gdf.copy() if base_gdf is not None else None,
                    dataset_type=dataset,
                    target_month=TARGET_MONTH,
                    is_statewide=is_statewide
                )
                result, ok = process_and_upload_last_month(
                    target_year=TARGET_YEAR,
                    target_month=TARGET_MONTH,
                    gdf=gdf,
                    climo_cache=climo,
                    climo_ytd_cache=climo_ytd,
                    dataset_type=dataset,
                    is_statewide=is_statewide
                )

            print("\nUpload Result:")
            print(result)
            if not ok:
                any_failed = True

    if any_failed:
        print("\nERROR: One or more uploads failed.")
        sys.exit(1)