"""
סורק הגרלות מחיר מטרה - dira.moch.gov.il
מחשב סיכויי זכייה ומייצר דוח HTML יומי.
רץ מקומית או דרך GitHub Actions.
"""

import sys
import io
import os
import webbrowser
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

# ב-CI פלט לקובץ index.html (GitHub Pages), אחרת lottery_report.html
IS_CI = os.environ.get("CI") == "true"
OUTPUT_FILE = Path(__file__).parent / ("index.html" if IS_CI else "lottery_report.html")

if not IS_CI:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE_API = (
    "https://dira.moch.gov.il/api/Invoker"
    "?method=Projects"
    "&param=%3FfirstApplicantIdentityNumber%3D"
    "%26secondApplicantIdentityNumber%3D"
    "%26ProjectStatus%3D4"
    "%26Entitlement%3D1"
    "%26PageNumber%3D{page}"
    "%26PageSize%3D{size}"
    "%26IsInit%3Dtrue%26"
)


def fetch_all_projects():
    all_projects = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="he-IL",
        )
        page = context.new_page()

        print("טוען את האתר...")
        page.goto("https://dira.moch.gov.il/ProjectsList", wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2000)

        page_num = 1
        page_size = 100
        total_records = None

        while True:
            url = BASE_API.format(page=page_num, size=page_size)
            print(f"מביא עמוד {page_num}...")

            result = page.evaluate(f"""
                async () => {{
                    const res = await fetch("{url}");
                    return await res.json();
                }}
            """)

            items = result.get("ProjectItems", [])
            all_projects.extend(items)

            if total_records is None:
                total_records = result.get("NumOfRecords", 0)
                print(f"סה\"כ {total_records} פרויקטים")

            if len(all_projects) >= total_records or len(items) == 0:
                break
            page_num += 1

        browser.close()

    return all_projects


def parse_project(row):
    try:
        apartments = int(row.get("LotteryApparmentsNum") or 0)
        registered = int(row.get("TotalSubscribers") or 0)
        if apartments <= 0 or registered <= 0:
            return None

        end_date = row.get("ApplicationEndDate", "")
        if isinstance(end_date, str) and "T" in end_date:
            end_date = end_date.split("T")[0]

        return {
            "id": row.get("LotteryNumber", ""),
            "city": row.get("CityDescription", ""),
            "contractor": row.get("ContractorDescription", ""),
            "apartments": apartments,
            "registered": registered,
            "end_date": end_date,
            "probability": apartments / registered * 100,
            "price_per_sqm": row.get("PricePerUnit", 0),
            "entitlement": row.get("EntitlementDescription", ""),
        }
    except (ValueError, TypeError):
        return None


def row_color(prob):
    if prob >= 5:
        return "#d4edda"
    if prob >= 2:
        return "#fff3cd"
    return "#f8d7da"


def generate_html(projects, repo_url=""):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    sorted_p = sorted(projects, key=lambda x: x["probability"], reverse=True)

    rows_html = ""
    for p in sorted_p:
        color = row_color(p["probability"])
        contractor = p["contractor"]
        if len(contractor) > 35:
            contractor = contractor[:33] + "..."
        price = f'₪{p["price_per_sqm"]:,.0f}' if p["price_per_sqm"] else "-"
        rows_html += f"""
        <tr style="background:{color}">
          <td>{p['id']}</td>
          <td>{p['city']}</td>
          <td title="{p['contractor']}">{contractor}</td>
          <td>{p['entitlement']}</td>
          <td>{p['apartments']:,}</td>
          <td>{p['registered']:,}</td>
          <td><strong>{p['probability']:.2f}%</strong></td>
          <td>{p['end_date']}</td>
          <td>{price}</td>
        </tr>"""

    update_btn = ""
    if repo_url:
        actions_url = f"{repo_url}/actions/workflows/update.yml"
        update_btn = f'<a href="{actions_url}" target="_blank" style="padding:8px 20px; background:#1a5276; color:white; border-radius:6px; text-decoration:none; font-size:14px;">עדכן עכשיו ↗</a>'
    else:
        update_btn = '<a href="עדכן.bat" style="padding:8px 20px; background:#1a5276; color:white; border-radius:6px; text-decoration:none; font-size:14px;">עדכן עכשיו</a>'

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>הגרלות מחיר מטרה</title>
  <style>
    body {{ font-family: Arial, sans-serif; direction: rtl; margin: 20px; background: #f0f4f8; }}
    h1 {{ color: #1a5276; margin: 0; }}
    .header {{ display: flex; align-items: center; gap: 16px; margin-bottom: 4px; flex-wrap: wrap; }}
    .subtitle {{ color: #666; margin-bottom: 12px; font-size: 14px; }}
    table {{ border-collapse: collapse; width: 100%; background: white;
             box-shadow: 0 2px 8px rgba(0,0,0,.12); border-radius: 8px; overflow: hidden; }}
    th {{ background: #1a5276; color: white; padding: 11px 14px; text-align: right; font-size: 14px; }}
    td {{ padding: 9px 14px; border-bottom: 1px solid #e8e8e8; font-size: 14px; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover {{ filter: brightness(0.96); }}
    .legend {{ margin: 10px 0 14px; }}
    .legend span {{ display: inline-block; padding: 4px 14px; border-radius: 20px; margin-left: 8px; font-size: 13px; }}
    .green  {{ background: #d4edda; }}
    .yellow {{ background: #fff3cd; }}
    .red    {{ background: #f8d7da; }}
    .note   {{ margin-top: 16px; color: #888; font-size: 12px; }}
    @media (max-width: 700px) {{ td, th {{ padding: 6px 8px; font-size: 12px; }} }}
  </style>
</head>
<body>
  <div class="header">
    <h1>הגרלות מחיר מטרה</h1>
    {update_btn}
  </div>
  <p class="subtitle">עודכן: {now} &nbsp;|&nbsp; סה"כ {len(sorted_p)} הגרלות פתוחות</p>
  <div class="legend">
    <span class="green">ירוק &gt;5%</span>
    <span class="yellow">צהוב 2%–5%</span>
    <span class="red">אדום &lt;2%</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>מספר הגרלה</th>
        <th>יישוב</th>
        <th>קבלן</th>
        <th>זכאות</th>
        <th>דירות</th>
        <th>נרשמים</th>
        <th>סיכוי זכייה</th>
        <th>סיום הרשמה</th>
        <th>מחיר למ"ר</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="note">סיכוי = דירות ÷ נרשמים × 100 &nbsp;|&nbsp; ממוין מהסיכוי הגבוה לנמוך</p>
</body>
</html>"""


def main():
    print("סורק הגרלות מחיר מטרה...")
    raw = fetch_all_projects()
    print(f"התקבלו {len(raw)} שורות מה-API")

    projects = [p for r in raw if (p := parse_project(r))]
    print(f"חולצו {len(projects)} הגרלות עם נתוני נרשמים")

    # ב-CI מעביר את כתובת ה-repo דרך env var (GITHUB_REPOSITORY)
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    repo_url = f"https://github.com/{gh_repo}" if gh_repo else ""

    html = generate_html(projects, repo_url=repo_url)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"דוח נשמר: {OUTPUT_FILE}")

    if not IS_CI:
        webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
