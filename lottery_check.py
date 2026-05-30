"""
סורק הגרלות מחיר מטרה - dira.moch.gov.il
מחשב סיכויי זכייה ומייצר דוח HTML + PWA יומי.
"""

import sys
import io
import os
import json
import webbrowser
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

IS_CI = os.environ.get("CI") == "true"
BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / ("index.html" if IS_CI else "lottery_report.html")

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
                print(f'סה"כ {total_records} פרויקטים')

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


def group_by_city(projects):
    """מקבץ הגרלות לפי עיר ומחזיר שורה סיכום לכל עיר."""
    cities = {}
    for p in projects:
        city = p["city"]
        if city not in cities:
            cities[city] = {
                "city": city,
                "apartments": 0,
                "registered": 0,
                "lotteries": 0,
                "end_date": p["end_date"],
                "entitlements": set(),
            }
        cities[city]["apartments"] += p["apartments"]
        cities[city]["registered"] += p["registered"]
        cities[city]["lotteries"] += 1
        cities[city]["entitlements"].add(p["entitlement"])
        # שמור את תאריך הסיום הרחוק ביותר
        if p["end_date"] > cities[city]["end_date"]:
            cities[city]["end_date"] = p["end_date"]

    result = []
    for c in cities.values():
        c["probability"] = c["apartments"] / c["registered"] * 100
        c["entitlement"] = " / ".join(sorted(c["entitlements"]))
        result.append(c)
    return result


def row_color(prob):
    if prob >= 5:
        return "#d4edda"
    if prob >= 2:
        return "#fff3cd"
    return "#f8d7da"


def write_pwa_files(base_dir):
    """יוצר manifest.json ו-sw.js עבור PWA."""
    manifest = {
        "name": "הגרלות מחיר מטרה",
        "short_name": "הגרלות",
        "description": "סיכויי זכייה בהגרלות מחיר מטרה",
        "start_url": ".",
        "display": "standalone",
        "background_color": "#f0f4f8",
        "theme_color": "#1a5276",
        "lang": "he",
        "dir": "rtl",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    (base_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Service worker — network-first (תמיד מביא עדכון טרי)
    cache_ver = datetime.now().strftime("%Y%m%d%H%M")
    sw = f"""const CACHE = 'hagralot-{cache_ver}';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => {{
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
}});
self.addEventListener('fetch', e => {{
  e.respondWith(
    fetch(e.request)
      .then(res => {{
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      }})
      .catch(() => caches.match(e.request))
  );
}});
"""
    (base_dir / "sw.js").write_text(sw, encoding="utf-8")

    # אייקון SVG פשוט — ממיר ל-PNG דרך base64 בתוך HTML
    # נשתמש ב-SVG כ-icon (חלק מהדפדפנים תומכים)
    svg_icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <rect width="192" height="192" rx="32" fill="#1a5276"/>
  <text x="96" y="130" font-size="110" text-anchor="middle" fill="white">🏠</text>
</svg>"""
    (base_dir / "icon.svg").write_text(svg_icon, encoding="utf-8")


def generate_html(projects, repo_url=""):
    now_dt = datetime.now()
    tz_note = " (UTC)" if IS_CI else ""
    now_str = now_dt.strftime(f"%d/%m/%Y %H:%M{tz_note}")
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S")

    cities = group_by_city(projects)
    sorted_cities = sorted(cities, key=lambda x: x["probability"], reverse=True)

    rows_html = ""
    for i, c in enumerate(sorted_cities, 1):
        color = row_color(c["probability"])
        lot_label = f'{c["lotteries"]} הגרלות' if c["lotteries"] > 1 else "הגרלה 1"
        rows_html += f"""
        <tr style="background:{color}">
          <td style="font-weight:bold;color:#1a5276">{i}</td>
          <td style="font-weight:bold">{c['city']}</td>
          <td style="color:#666;font-size:12px">{lot_label}</td>
          <td>{c['apartments']:,}</td>
          <td>{c['registered']:,}</td>
          <td><strong>{c['probability']:.2f}%</strong></td>
          <td>{c['end_date']}</td>
        </tr>"""

    update_btn = ""

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>הגרלות מחיר מטרה</title>
  <link rel="manifest" href="manifest.json">
  <meta name="theme-color" content="#1a5276">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="הגרלות">
  <link rel="apple-touch-icon" href="icon.svg">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, sans-serif; direction: rtl; background: #f0f4f8; padding: 16px; }}
    .header {{ background: #1a5276; color: white; border-radius: 12px; padding: 16px 20px;
               margin-bottom: 14px; }}
    .header h1 {{ font-size: 20px; margin-bottom: 6px; }}
    .update-bar {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
    .update-time {{ font-size: 13px; opacity: 0.85; }}
    .update-time time {{ font-weight: bold; }}
    .btn {{ padding: 6px 16px; background: rgba(255,255,255,0.2); color: white;
             border-radius: 20px; text-decoration: none; font-size: 13px;
             border: 1px solid rgba(255,255,255,0.4); white-space: nowrap; }}
    .btn:hover {{ background: rgba(255,255,255,0.35); }}
    .legend {{ margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; }}
    .legend span {{ padding: 4px 12px; border-radius: 20px; font-size: 13px; }}
    .green  {{ background: #d4edda; }}
    .yellow {{ background: #fff3cd; }}
    .red    {{ background: #f8d7da; }}
    .table-wrap {{ overflow-x: auto; border-radius: 10px;
                   box-shadow: 0 2px 8px rgba(0,0,0,.1); }}
    table {{ border-collapse: collapse; width: 100%; background: white; min-width: 600px; }}
    th {{ background: #1a5276; color: white; padding: 11px 12px; text-align: right; font-size: 13px; }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #eee; font-size: 13px; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover {{ filter: brightness(0.96); }}
    .note {{ margin-top: 12px; color: #999; font-size: 11px; text-align: center; }}
    .count {{ font-size: 13px; opacity: 0.75; margin-top: 2px; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>🏠 הגרלות מחיר מטרה</h1>
    <p class="count">{len(sorted_cities)} ערים | {len(projects)} הגרלות</p>
    <div class="update-bar" style="margin-top:10px">
      <span class="update-time">עודכן: <time datetime="{now_iso}">{now_str}</time></span>
      {update_btn}
    </div>
  </div>
  <div class="legend">
    <span class="green">ירוק &gt;5%</span>
    <span class="yellow">צהוב 2%–5%</span>
    <span class="red">אדום &lt;2%</span>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>עיר</th>
          <th>הגרלות</th>
          <th>סה"כ דירות</th>
          <th>סה"כ נרשמים</th>
          <th>סיכוי זכייה</th>
          <th>סיום הרשמה</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
  <div id="install-bar" style="display:none; margin-top:14px; text-align:center">
    <button id="install-btn" style="padding:10px 24px; background:#1a5276; color:white;
      border:none; border-radius:24px; font-size:15px; cursor:pointer;">
      📲 הוסף למסך הבית
    </button>
  </div>
  <p id="ios-hint" style="display:none; margin-top:14px; text-align:center;
     font-size:13px; color:#555;">
    באייפון: לחץ ↑ שיתוף ← "הוסף למסך הבית"
  </p>
  <p class="note" style="margin-top:14px">מתעדכן אוטומטית כל שעה &nbsp;•&nbsp; סיכוי = סה"כ דירות בעיר ÷ סה"כ נרשמים × 100</p>
  <p class="note" style="margin-top:6px">Built by Lior Yehuda</p>
  <script>
    // מבטל service workers ישנים שגורמים לדף לבן
    if ('serviceWorker' in navigator) {{
      navigator.serviceWorker.getRegistrations().then(regs => {{
        regs.forEach(r => r.unregister());
      }});
    }}
    // המר שעת UTC לשעון מקומי של המשתמש
    document.querySelectorAll('time[datetime]').forEach(el => {{
      const d = new Date(el.getAttribute('datetime') + 'Z');
      el.textContent = d.toLocaleString('he-IL', {{
        day:'2-digit', month:'2-digit', year:'numeric',
        hour:'2-digit', minute:'2-digit'
      }});
    }});

    // כפתור התקנה - אנדרואיד
    let deferredPrompt;
    window.addEventListener('beforeinstallprompt', e => {{
      e.preventDefault();
      deferredPrompt = e;
      document.getElementById('install-bar').style.display = 'block';
    }});
    document.getElementById('install-btn')?.addEventListener('click', async () => {{
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const {{ outcome }} = await deferredPrompt.userChoice;
      deferredPrompt = null;
      if (outcome === 'accepted') {{
        document.getElementById('install-bar').style.display = 'none';
      }}
    }});
    window.addEventListener('appinstalled', () => {{
      document.getElementById('install-bar').style.display = 'none';
    }});

    // הוראה לאייפון
    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches;
    if (isIOS && !isStandalone) {{
      document.getElementById('ios-hint').style.display = 'block';
    }}
  </script>
</body>
</html>"""


def main():
    print("סורק הגרלות מחיר מטרה...")
    raw = fetch_all_projects()
    print(f"התקבלו {len(raw)} שורות מה-API")

    projects = [p for r in raw if (p := parse_project(r))]
    print(f"חולצו {len(projects)} הגרלות עם נתוני נרשמים")

    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    repo_url = f"https://github.com/{gh_repo}" if gh_repo else ""

    write_pwa_files(BASE_DIR)

    html = generate_html(projects, repo_url=repo_url)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"דוח נשמר: {OUTPUT_FILE}")

    if not IS_CI:
        webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
