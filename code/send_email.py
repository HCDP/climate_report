import datetime
import re
import argparse
import time
import requests
import os

token = os.getenv("HCDP_API_TOKEN")

headers = {
    "Authorization": f"Bearer {token}"
}

def fetch_with_retry(url, params=None, retries=3, wait=10):
    """GET with retry on timeout. Raises on final failure."""
    for attempt in range(1, retries + 1):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=30)
            res.raise_for_status()
            return res
        except requests.exceptions.Timeout:
            if attempt < retries:
                print(f"  Timeout on attempt {attempt}/{retries} — retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
        except requests.exceptions.RequestException:
            raise

def escape_commas(value):
    """Escape unescaped commas so the API doesn't treat them as delimiters."""
    return re.sub(r'(?<!\\),', r'\\,', value)

def get_ordinal(n):
    if 11 <= n <= 13:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

def generate_rainfall_sentence(data_list, location_name, total_years):
  if not data_list:
      return f"{location_name} rainfall data is not yet available for this period."

  record = data_list[0]
  mean_val = record.get("mean")
  anomaly_raw = float(record.get("anomaly", 0))
  pchange_raw = float(record.get("pchange", 0))
  rank_str = record.get("rank")
  date_str = record.get("date")

  dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")
  month_name = dt.strftime("%B")

  sign = "+" if anomaly_raw >= 0 else "-"
  abs_anomaly = abs(anomaly_raw)
  abs_pchange = abs(pchange_raw)

  sentence = (
      f"{location_name} received {float(mean_val):.1f} inches of rainfall; "
      f"{sign}{abs_anomaly:.1f} inches ({sign}{abs_pchange:.1f}%) from the {month_name} average"
  )

  if rank_str and str(rank_str).isdigit():
      rank = int(rank_str)
      dry_rank = total_years - rank + 1

      if rank <= 30:
          sentence += f", ranking as the {get_ordinal(rank)} wettest {month_name} in the last {total_years} years."
      elif dry_rank <= 30:
          sentence += f", ranking as the {get_ordinal(dry_rank)} driest {month_name} in the last {total_years} years."
      else:
          sentence += f", near normal for {month_name}."
  else:
      sentence += "."

  return sentence


def generate_temperature_sentence(data_list, location_name, total_years):
    if not data_list:
        return f"{location_name} temperature data is not yet available for this period."

    record = data_list[0]
    mean_val = record.get("mean")
    anomaly_raw = float(record.get("anomaly", 0))
    rank_str = record.get("rank")
    max_val = record.get("max")
    date_str = record.get("date")

    dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")
    month_name = dt.strftime("%B")

    sign = "+" if anomaly_raw >= 0 else "-"
    abs_anomaly = abs(anomaly_raw)

    sentence = f"{location_name} averaged {float(mean_val):.1f}°F"
    if max_val is not None:
        sentence += f" (max {float(max_val):.1f}°F)"
    sentence += f"; {sign}{abs_anomaly:.1f}°F from the {month_name} average"

    # rank 1 = warmest (anomaly ranked descending); high rank = coolest
    if rank_str is not None and str(rank_str).replace(".", "", 1).isdigit():
        rank = int(float(rank_str))

        if rank <= (total_years / 2):
            condition = "warmest"
            display_rank = get_ordinal(rank)
        else:
            cool_rank = total_years - rank + 1
            condition = "coolest"
            display_rank = get_ordinal(cool_rank)

        sentence += f", ranking as the {display_rank} {condition} {month_name} in the last {total_years} years."
    else:
        sentence += "."

    return sentence


def generate_drought_sentence(data_list, location_name):
    if not data_list:
        return f"{location_name} drought data is not yet available for this period."

    record = data_list[0]
    date_str = record.get("date")

    def get_pct(key):
        val = record.get(key) or record.get(key.lower()) or record.get(key.upper())
        return float(val) if val is not None else 0.0

    d4 = get_pct("d4")
    d3 = get_pct("d3")
    d2 = get_pct("d2")
    d1 = get_pct("d1")
    d0 = get_pct("d0")
    near_normal = get_pct("Near Normal")
    w0 = get_pct("w0")
    w1 = get_pct("w1")
    w2 = get_pct("w2")
    w3 = get_pct("w3")
    w4 = get_pct("w4")

    total_drought = d1 + d2 + d3 + d4
    total_wet = w0 + w1 + w2 + w3 + w4
    severe_drought = d2 + d3 + d4

    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, TypeError):
        try:
            dt = datetime.datetime.strptime(date_str[:7], "%Y-%m")
        except (ValueError, TypeError):
            dt = None
    month_name = dt.strftime("%B") if dt else ""

    if near_normal >= 50:
        sentence = (
            f"{location_name} was predominantly near-normal "
            f"({near_normal:.0f}%), with {total_drought:.0f}% in drought "
            f"and {total_wet:.0f}% wetter than normal."
        )
    elif total_drought >= total_wet:
        if severe_drought >= 20:
            sentence = (
                f"{location_name} experienced drought conditions "
                f"affecting {total_drought:.0f}% of the area, with {severe_drought:.0f}% "
                f"in severe to exceptional drought (D2–D4)."
            )
        else:
            sentence = (
                f"{location_name} experienced drought conditions "
                f"affecting {total_drought:.0f}% of the area."
            )
    else:
        sentence = (
            f"{location_name} experienced wetter than normal conditions "
            f"across {total_wet:.0f}% of the area."
        )

    return sentence

SUBSCRIPTIONS_URL = "https://api.hcdp.ikewai.org/mesonet/climate_report/subscriptions"

DATA_SOURCES = [
    {
        "key": "rainfall",
        "url": "https://api.hcdp.ikewai.org/mesonet/climate_report/rainfall_stats",
        "sentence_fn": generate_rainfall_sentence,
        "start_year": 1920,
        "label": "Rainfall",
    },
    {
        "key": "temperature",
        "url": "https://api.hcdp.ikewai.org/mesonet/climate_report/temperature_stats",
        "sentence_fn": generate_temperature_sentence,
        "start_year": 1990,
        "label": "Temperature",
    },
    {
        "key": "drought",
        "url": "https://api.hcdp.ikewai.org/mesonet/climate_report/drought_stats",
        "sentence_fn": generate_drought_sentence,
        "start_year": None,
        "label": "Drought",
    },
]

LABELS = {s["key"]: s["label"] for s in DATA_SOURCES}

# 2. Helper function to get "last month" in YYYY-MM format
def get_last_month_str():
    today = datetime.date.today()
    first_day_this_month = today.replace(day=1)
    last_month = first_day_this_month - datetime.timedelta(days=1)
    return last_month.strftime("%Y-%m")

def call_sentence_fn(source, data_list, name):
    if source["start_year"] is not None:
        return source["sentence_fn"](data_list, name, source["total_years"])
    return source["sentence_fn"](data_list, name)

def bold_stats(sentence):
    sentence = re.sub(
        r'(\d+(?:st|nd|rd|th)\s+(?:wettest|driest|warmest|coolest))',
        r'<strong>\1</strong>',
        sentence
    )
    sentence = re.sub(r'(\d+\.?\d*\s*(?:inches|°F))', r'<strong>\1</strong>', sentence)
    sentence = re.sub(r'(\d+\.?\d*%)', r'<strong>\1</strong>', sentence)
    return sentence

def group_by_island(reports):
    """Group reports by island, island-level entry first within each group."""
    groups = {}
    for report in reports:
        island = report["query"].get("island", "")
        if island not in groups:
            groups[island] = []
        groups[island].append(report)
    for island in groups:
        groups[island].sort(key=lambda r: (r["query"]["division_type"] != "island"))
    return groups

def render_sentences(report, html=False):
    results = []
    for key in ["rainfall", "temperature", "drought"]:
        entry = report.get(key)
        if not entry or not entry.get("summary_sentence"):
            continue
        sentence = entry["summary_sentence"]
        if html:
            results.append(
                f"<p style='margin:4px 0;'><strong>{LABELS[key]}:</strong> {bold_stats(sentence)}</p>"
            )
        else:
            results.append(f"{LABELS[key]}: {sentence}")
    return "\n".join(results) if not html else "".join(results)

def display_island(island):
    return "Hawaiʻi Island" if island in ("Hawaii", "Hawaiʻi") else island

def display_div_type(div_type):
    return {"ahupuaa": "Ahupuaʻa"}.get(div_type, div_type.capitalize())

def build_email_content(user_data, target_date=None):
    statewide = user_data.get("statewide", {})
    reports = user_data.get("reports", [])
    island_groups = group_by_island(reports)

    # --- Plain text ---
    lines = [
        "Hawaii Climate Report", "=" * 40, "", "STATEWIDE SUMMARY", "-" * 20
    ]
    for key in ["rainfall", "temperature", "drought"]:
        sentence = statewide.get(key, "")
        if sentence:
            lines.append(f"{LABELS[key]}: {sentence}")
    lines.append("")
    lines.append("YOUR LOCATIONS")
    lines.append("=" * 40)

    for island, group in island_groups.items():
        lines.append(f"\n{'=' * 6} {display_island(island)} {'=' * 6}")
        for report in group:
            q = report.get("query", {})
            div_type = q.get("division_type", "")
            name = q.get("name", "")
            if div_type == "island":
                lines.append("\n  Island statistics")
            else:
                lines.append(f"\n  {name} ({display_div_type(div_type)})")
            lines.append(render_sentences(report, html=False))
        lines.append("")

    text = "\n".join(lines)

    # --- HTML ---
    statewide_html = "".join(
        f"<p><strong>{LABELS[k]}:</strong> {bold_stats(statewide[k])}</p>"
        for k in ["rainfall", "temperature", "drought"] if statewide.get(k)
    )

    island_blocks = ""
    for island, group in island_groups.items():
        entries_html = ""
        for i, report in enumerate(group):
            q = report.get("query", {})
            div_type = q.get("division_type", "")
            name = q.get("name", "")
            border = "border-top:1px solid #c5dff0;" if i > 0 else ""
            sentences_html = render_sentences(report, html=True)

            if div_type == "island":
                entries_html += f"""
                <div style="padding:16px 20px;background:#f0f7fc;{border}">
                    <h4 style="margin:0 0 8px 0;color:#1a5276;font-size:15px;">
                        Island-wide
                    </h4>
                    {sentences_html}
                </div>"""
            else:
                entries_html += f"""
                <div style="padding:16px 20px;{border}">
                    <h4 style="margin:0 0 8px 0;color:#1a5276;font-size:15px;">
                        {name} <span style="font-size:13px;font-weight:normal;color:#666;">({display_div_type(div_type)})</span>
                    </h4>
                    {sentences_html}
                </div>"""

        island_blocks += f"""
        <div style="margin-bottom:28px;border:1px solid #c5dff0;border-radius:8px;overflow:hidden;">
            <div style="background:#1a5276;color:white;padding:12px 20px;">
                <h3 style="margin:0;font-size:17px;">{display_island(island)}</h3>
            </div>
            {entries_html}
        </div>"""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:auto;color:#222;">
        <div style="text-align:center;margin-bottom:24px;">
            <img src="https://www.hawaii.edu/climate-data-portal/wp-content/uploads/2022/03/cropped-HCDP_logo_crop_attempt-beta.png"
                 alt="Hawaii Climate Data Portal"
                 style="max-width:300px;width:100%;height:auto;" />
        </div>
        <h1 style="color:#1a5276;">Hawaiʻi Climate Summary &#8212; {f'{datetime.datetime.strptime(target_date, "%Y-%m").strftime("%B %Y")}' if target_date else ''}</h1>
        <div style="background:#eaf4fb;border-left:4px solid #1a5276;padding:16px 20px;margin-bottom:28px;border-radius:4px;">
            <h2 style="margin-top:0;color:#1a5276;">Statewide Summary</h2>
            {statewide_html}
        </div>
        <h2 style="color:#1a5276;">Your Locations</h2>
        {island_blocks}
        <div style="margin-top:32px;padding:20px;background:#f0f7fc;border-radius:8px;text-align:center;border:1px solid #c5dff0;">
            <p style="margin:0 0 10px 0;font-size:15px;">Want to explore more data or add locations to your summary?</p>
            <a href="https://www.hawaii.edu/climate-data-portal/climate-summary/"
               style="display:inline-block;background:#1a5276;color:white;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:15px;font-weight:bold;">
                Visit the Hawaiʻi Climate Summary Page
            </a>
        </div>
    </div>"""

    return text, html


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Send monthly climate report emails.")
    arg_parser.add_argument("--email", type=str, nargs="+", default=None, help="If provided, only send to these addresses.")
    args = arg_parser.parse_args()
    TARGET_EMAILS = args.email
    if TARGET_EMAILS is None:
        email_env = os.getenv("TARGET_EMAILS")
        if email_env is not None:
            email_env = set([value.strip() for value in email_env.split(",")])
        TARGET_EMAILS = email_env
    else:
        TARGET_EMAILS = set(TARGET_EMAILS)

    target_date = get_last_month_str()
    target_year = int(target_date.split("-")[0])
    print(f"Querying data for target date: {target_date}\n" + "=" * 50)

    # Compute total_years per source
    for source in DATA_SOURCES:
        if source["start_year"] is not None:
            source["total_years"] = target_year - source["start_year"] + 1

    # 3. Fetch statewide sentences once
    statewide_params = {"date": target_date, "division_type": "Statewide"}
    statewide_sentences = {}
    statewide_ok = True
    print("Fetching statewide summaries...")
    for source in DATA_SOURCES:
        try:
            res = fetch_with_retry(source["url"], params=statewide_params)
            data_payload = res.json()
            data_list = data_payload if isinstance(data_payload, list) else data_payload.get("data", [])
            if not data_list:
                print(f"  [{source['key']}] WARNING: No statewide data returned.")
                statewide_ok = False
                statewide_sentences[source["key"]] = None
            else:
                statewide_sentences[source["key"]] = call_sentence_fn(source, data_list, "Hawaiʻi")
                print(f"  [{source['key']}] {statewide_sentences[source['key']]}")
        except requests.exceptions.RequestException as e:
            print(f"  [{source['key']}] ERROR fetching statewide data: {e}")
            statewide_ok = False
            statewide_sentences[source["key"]] = None

    if not statewide_ok:
        print("WARNING: Some statewide data is missing. Emails will be skipped.")

    # 4. Fetch subscriptions
    print("\nFetching subscriptions...")
    res = fetch_with_retry(SUBSCRIPTIONS_URL)
    subscriptions = res.json()

    # Islands first, then finer divisions
    division_types = ["island", "moku", "climate", "ahupuaa", "watershed"]
    user_reports = {}

    # 5. Build a personalized report for each subscriber
    for user in subscriptions:
        user_id = user.get("id")
        user_email = user.get("email")

        if TARGET_EMAILS and user_email not in TARGET_EMAILS:
            print(f"\nSkipping {user_email} — not in target addresses.")
            continue

        print(f"\nProcessing User: {user_email} (ID: {user_id})")
        print("-" * 50)

        user_reports[user_id] = {
            "email": user_email,
            "statewide": statewide_sentences,
            "reports": [],
            "all_data_ok": True,
        }

        for div_type in division_types:
            locations = user.get(div_type, [])

            for loc in locations:
                if "::" in loc:
                    island, name = loc.split("::", 1)
                else:
                    island = loc
                    name = loc

                print(f"  -> {div_type.upper()} | Island: {island} | Name: {name}")

                if island == "Statewide":
                    for key, sentence in statewide_sentences.items():
                        print(f"     [{key}] {sentence}")
                    continue

                query_params = {
                    "date": target_date,
                    "division_type": div_type,
                    "island": island,
                    "name": name,
                }
                api_params = {
                    **query_params,
                    "island": escape_commas(island),
                    "name": escape_commas(name),
                }

                location_report = {
                    "query": query_params,
                    "rainfall": None,
                    "temperature": None,
                    "drought": None,
                }

                for source in DATA_SOURCES:
                    try:
                        stats_res = fetch_with_retry(source["url"], params=api_params)
                        data_payload = stats_res.json()
                        data_list = data_payload if isinstance(data_payload, list) else data_payload.get("data", [])
                        if not data_list:
                            print(f"     [{source['key']}] No data returned for {name}")
                            user_reports[user_id]["all_data_ok"] = False
                        summary = call_sentence_fn(source, data_list, name)
                        print(f"     [{source['key']}] {summary}")
                        location_report[source["key"]] = {
                            "status": "success",
                            "summary_sentence": summary,
                            "data": data_payload,
                        }
                    except requests.exceptions.RequestException as e:
                        print(f"     [{source['key']}] ERROR: {e}")
                        user_reports[user_id]["all_data_ok"] = False
                        location_report[source["key"]] = {
                            "status": "error",
                            "summary_sentence": call_sentence_fn(source, [], name),
                            "error_message": str(e),
                        }

                user_reports[user_id]["reports"].append(location_report)

    # 6. Send a personalized email to each subscriber
    print("\n" + "=" * 50)
    if TARGET_EMAILS is not None:
        print(f"Sending emails... (target override: {', '.join(TARGET_EMAILS)})")
    else:
        print("Sending emails to all subscribers...")
    for user_id, user_data in user_reports.items():
        email = user_data.get("email")
        if TARGET_EMAILS and email not in TARGET_EMAILS:
            print(f"Skipping {email} — not in target addresses.")
            continue
        if not statewide_ok:
            print(f"Skipping email to {email} — statewide data was not available.")
            continue
        if not user_data.get("all_data_ok", True):
            print(f"Skipping email to {email} — one or more locations returned no data.")
            continue
        text_content, html_content = build_email_content(user_data, target_date)
        body = {"text": text_content, "html": html_content}
        url = f"https://api.hcdp.ikewai.org/mesonet/climate_report/subscription/{user_id}/email"
        try:
            res = requests.post(url, json=body, headers=headers)
            res.raise_for_status()
            print(f"Success! Email sent to {email} (ID: {user_id})")
        except requests.exceptions.RequestException as e:
            print(f"Failed to send email to {email} ({user_id}): {e}")