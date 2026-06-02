"""
Backfill ytd_pnormal for all historical May rainfall records.

For each year that has monthly rainfall TIFs Jan–May:
  1. Generate a YTD May raster (sum Jan–May) via make_ytd_rainfall.
  2. Compare to the climatology YTD raster to compute ytd_pnormal.
  3. Re-upload all years' May records (mean, anomaly, pchange, rank, ytd_pnormal)
     so ranks are consistent and ytd is populated for every year.

Usage:
    python backfill_ytd.py [--month 5] [--dry-run]
"""

import os
import sys
import glob
import json
import shutil
import argparse
import warnings

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
import requests
from rasterstats import zonal_stats

# Reuse YTD generation logic from get_maps
sys.path.insert(0, os.path.dirname(__file__))
from get_maps import make_ytd_rainfall

RF_SOURCE_DIR = "/Users/cherryleheu/Documents/HCDP/Data/rf_all/monthly"

warnings.filterwarnings('ignore')

# ── env ──────────────────────────────────────────────────────────────────────
hcdp_api_token = os.environ.get('HCDP_API_TOKEN')
local_dep_dir   = os.environ.get('DEPENDENCY_DIR')
local_output_dir = os.environ.get('OUTPUT_DIR')

HEADERS = {
    "Authorization": f"Bearer {hcdp_api_token}",
    "Content-Type": "application/json; charset=utf-8"
}

RAINFALL_API_URL = "https://api.hcdp.ikewai.org/mesonet/climate_report/rainfall_stats"
CHUNK_SIZE = 500

ALL_DIVISIONS = ['statewide', 'island', 'climate', 'moku', 'ahupuaa', 'watershed']

# ── helpers ───────────────────────────────────────────────────────────────────
def upload_chunks(url, records, timeout=60):
    statuses = []
    for i in range(0, len(records), CHUNK_SIZE):
        chunk = records[i:i + CHUNK_SIZE]
        payload = {"overwrite": True, "data": chunk}
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, headers=HEADERS, timeout=timeout)
                statuses.append(response.status_code)
                break
            except requests.exceptions.Timeout:
                if attempt == 2:
                    statuses.append("Timeout")
            except Exception as e:
                statuses.append(f"Error: {e}")
                break
    return statuses, all(s == 200 for s in statuses)


def convert_units(value):
    if value is None or np.isnan(value):
        return np.nan
    return value / 25.4  # mm → inches


def load_shapefile(division):
    shapefile = os.path.join(local_dep_dir, "shapefiles", f"{division}.shp")
    gdf = gpd.read_file(shapefile, encoding='utf-8').copy()

    island_col = next((c for c in gdf.columns if c.lower() in ["island", "mokupuni", "isle", "islandname"]), None)
    name_col   = next((c for c in gdf.columns if c.lower() in ["name", "division", "moku", "climate_div", "ahupuaa", "county", "name_hwn"]), None)

    okina_regex = r"['`'']"
    if island_col:
        gdf[island_col] = gdf[island_col].replace(okina_regex, "ʻ", regex=True)
    if name_col:
        gdf[name_col] = gdf[name_col].replace(okina_regex, "ʻ", regex=True)

    gdf['geometry'] = gdf['geometry'].simplify(tolerance=0.001, preserve_topology=True)

    if island_col and name_col:
        is_dup = gdf.duplicated(subset=[island_col, name_col], keep=False)
        cum = gdf.groupby([island_col, name_col]).cumcount() + 1
        gdf.loc[is_dup, name_col] = gdf.loc[is_dup, name_col].astype(str) + " " + cum[is_dup].astype(str)
        gdf = gdf.dissolve(by=[island_col, name_col], as_index=False)
        gdf["island_clean"] = gdf[island_col]
        gdf["name_clean"]   = gdf[name_col]
    elif name_col:
        gdf = gdf.dissolve(by=name_col, as_index=False)
        gdf["name_clean"]   = gdf[name_col]
        gdf["island_clean"] = gdf[name_col] if division == "island" else "Statewide"

    gdf["division_type"] = division
    return gdf


def ensure_monthly_tifs(year, month):
    """Copy Jan–month TIFs from source dir into local_dep_dir if missing."""
    dest_dir = os.path.join(local_dep_dir, "rainfall")
    os.makedirs(dest_dir, exist_ok=True)
    for m in range(1, month + 1):
        fname = f"rainfall_{year}_{m:02d}.tif"
        dest = os.path.join(dest_dir, fname)
        if not os.path.exists(dest):
            src = os.path.join(RF_SOURCE_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, dest)
                print(f"  Copied {fname} from source dir")
            else:
                print(f"  Warning: {fname} not found in source dir or dep dir")


def to_row(row, cols):
    return [
        None if (isinstance(x, float) and np.isnan(x)) else (x.item() if hasattr(x, 'item') else x)
        for x in row[cols]
    ]


# ── core ──────────────────────────────────────────────────────────────────────
def backfill_division(month, division, dry_run):
    is_statewide = (division == "statewide")
    gdf = None if is_statewide else load_shapefile(division)

    rainfall_dir = os.path.join(local_dep_dir, "rainfall")
    climo_file   = os.path.join(local_dep_dir, "climo", "rainfall", f"rainfall_1991-2020_{month:02d}.tif")
    climo_ytd_file = os.path.join(local_dep_dir, "climo", "rainfall_ytd", f"YTD_rain_month_{month:02d}.tif")

    if not os.path.exists(climo_ytd_file):
        print(f"  ERROR: climo YTD file not found: {climo_ytd_file}")
        return False

    # ── reproject gdf to match raster CRS if needed ──
    if not is_statewide:
        with rasterio.open(climo_file) as src:
            raster_crs = src.crs
        if gdf.crs != raster_crs:
            gdf = gdf.to_crs(raster_crs)
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]

    # ── climatology (monthly) ──
    if is_statewide:
        with rasterio.open(climo_file) as src:
            arr = src.read(1).astype(float)
            arr = np.where(arr == src.nodata, np.nan, arr)
            climo_mean = convert_units(np.nanmean(arr))
    else:
        climo_zs = zonal_stats(vectors=gdf, raster=climo_file, stats=["mean"], nodata=None)
        climo_means = [convert_units(c["mean"]) if c["mean"] is not None else np.nan for c in climo_zs]
        gdf["climo_mean"] = climo_means

    # ── climatology (YTD) ──
    if is_statewide:
        with rasterio.open(climo_ytd_file) as src:
            ytd_arr = src.read(1).astype(float)
            ytd_arr = np.where(ytd_arr == -9999, np.nan, ytd_arr)
            climo_ytd_mean = np.nanmean(ytd_arr)
    else:
        ytd_climo_zs = zonal_stats(vectors=gdf, raster=climo_ytd_file, stats=["mean"], nodata=-9999)
        climo_ytd_means = [c["mean"] if c["mean"] is not None else np.nan for c in ytd_climo_zs]

    # ── loop all historical years ──
    all_records = []
    tif_pattern = os.path.join(rainfall_dir, f"rainfall_*_{month:02d}.tif")

    for tif in sorted(glob.glob(tif_pattern)):
        parts = os.path.basename(tif).replace(".tif", "").split("_")
        year = int(parts[1])
        date_str = f"{year}-{month:02d}"

        # ── ensure monthly TIFs exist, then generate YTD ──
        ensure_monthly_tifs(year, month)
        ytd_path = make_ytd_rainfall(year, month)
        if ytd_path is None or not os.path.exists(ytd_path):
            print(f"  Warning: could not generate YTD for {year}, skipping ytd_pnormal")
            ytd_path = None

        if is_statewide:
            with rasterio.open(tif) as src:
                arr = src.read(1).astype(float)
                arr = np.where(arr == src.nodata, np.nan, arr)
                mean_raw = np.nanmean(arr)

            mean_val = convert_units(mean_raw) if not np.isnan(mean_raw) else np.nan
            anomaly  = mean_val - climo_mean if not np.isnan(mean_val) and not np.isnan(climo_mean) else np.nan
            pchange  = ((mean_val - climo_mean) / climo_mean) * 100 if not np.isnan(anomaly) else np.nan

            ytd_pnormal = None
            if ytd_path:
                with rasterio.open(ytd_path) as src:
                    curr_arr = src.read(1).astype(float)
                    curr_arr = np.where(curr_arr == -9999, np.nan, curr_arr)
                    curr_mean = np.nanmean(curr_arr)
                if not np.isnan(curr_mean) and not np.isnan(climo_ytd_mean) and climo_ytd_mean != 0:
                    ytd_pnormal = int(round((curr_mean / climo_ytd_mean) * 100, 0))

            all_records.append({
                "island": "Statewide", "division_type": "Statewide", "name": "Statewide",
                "date": date_str, "year": year,
                "mean": round(mean_val, 2) if not np.isnan(mean_val) else None,
                "anomaly": round(anomaly, 2) if not np.isnan(anomaly) else None,
                "pchange": round(pchange, 2) if not np.isnan(pchange) else None,
                "ytd_pnormal": ytd_pnormal,
            })

        else:
            stats = zonal_stats(vectors=gdf, raster=tif, stats=["mean"], nodata=None)

            if ytd_path:
                ytd_zs = zonal_stats(vectors=gdf, raster=ytd_path, stats=["mean"], nodata=-9999)
            else:
                ytd_zs = [{"mean": None}] * len(gdf)

            for idx, row in gdf.iterrows():
                mean_raw   = stats[idx]["mean"]
                climo_mean_val = row["climo_mean"]

                if mean_raw is None or np.isnan(mean_raw):
                    mean_val = anomaly = pchange = np.nan
                else:
                    mean_val = convert_units(mean_raw)
                    if np.isnan(climo_mean_val):
                        anomaly = pchange = np.nan
                    else:
                        anomaly = mean_val - climo_mean_val
                        pchange = ((mean_val - climo_mean_val) / climo_mean_val) * 100

                curr_ytd_mean = ytd_zs[idx]["mean"]
                climo_ytd_val = climo_ytd_means[idx]
                if curr_ytd_mean is not None and not np.isnan(climo_ytd_val) and climo_ytd_val != 0:
                    ytd_pnormal = int(round((curr_ytd_mean / climo_ytd_val) * 100, 0))
                else:
                    ytd_pnormal = None

                all_records.append({
                    "island": row["island_clean"],
                    "division_type": row["division_type"],
                    "name": row["name_clean"],
                    "date": date_str, "year": year,
                    "mean": round(mean_val, 1) if not np.isnan(mean_val) else None,
                    "anomaly": round(anomaly, 1) if not np.isnan(anomaly) else None,
                    "pchange": round(pchange, 1) if not np.isnan(pchange) else None,
                    "ytd_pnormal": ytd_pnormal,
                })

    if not all_records:
        print(f"  No records found for month {month:02d}.")
        return False

    df = pd.DataFrame(all_records)
    df["rank"] = df.groupby(["island", "name"])["anomaly"].rank(method="min", ascending=False)

    cols = ["island", "division_type", "name", "date", "mean", "anomaly", "pchange", "rank", "ytd_pnormal"]
    final_data = [to_row(row, cols) for _, row in df[cols].iterrows()]

    n_chunks = max(1, (len(final_data) + CHUNK_SIZE - 1) // CHUNK_SIZE)

    if dry_run:
        print(f"  [DRY RUN] Would upload {len(final_data)} records in {n_chunks} chunk(s)")
        print(f"  Columns: {cols}")
        print("  Sample (last 3):")
        for r in final_data[-3:]:
            print(f"    {r}")
        return True

    print(f"  Uploading {len(final_data)} records in {n_chunks} chunk(s)...")
    statuses, all_ok = upload_chunks(RAINFALL_API_URL, final_data)
    print(f"  Statuses: {statuses}")
    return all_ok


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Backfill ytd_pnormal for all historical rainfall records for a given month.")
    arg_parser.add_argument('--month', type=int, default=5, help="Month to backfill (default: 5 for May)")
    arg_parser.add_argument('--division', type=str, default=None, help="Single division to run (default: all)")
    arg_parser.add_argument('--dry-run', action='store_true', help="Print what would be uploaded without uploading")
    args = arg_parser.parse_args()

    divisions = [args.division.strip().lower()] if args.division else ALL_DIVISIONS

    print(f"Backfilling ytd_pnormal for month {args.month:02d} | divisions: {divisions} | dry_run={args.dry_run}")

    any_failed = False
    for division in divisions:
        print(f"\n--- {division.upper()} ---")
        ok = backfill_division(args.month, division, args.dry_run)
        if not ok:
            any_failed = True

    if any_failed:
        print("\nERROR: One or more uploads failed.")
        sys.exit(1)
    else:
        print("\nDone.")
