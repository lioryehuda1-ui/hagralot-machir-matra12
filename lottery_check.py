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
        if p["end_date"] > cities[city]["end_date"]:
            cities[city]["end_date"] = p["end_date"]
        # ממוצע מחיר למ"ר משוקלל לפי דירות
        if p["price_per_sqm"]:
            cities[city]["price_sum"] = cities[city].get("price_sum", 0) + p["price_per_sqm"] * p["apartments"]
            cities[city]["price_count"] = cities[city].get("price_count", 0) + p["apartments"]

    result = []
    for c in cities.values():
        c["probability"] = c["apartments"] / c["registered"] * 100
        c["entitlement"] = " / ".join(sorted(c["entitlements"]))

        # חישובים פיננסיים (רוכש ראשון: 25% הון עצמי, משכנתא 75%)
        avg_sqm = c.get("price_sum", 0) / c.get("price_count", 1) if c.get("price_count") else 0
        apt_size = 107  # מ"ר ממוצע (4 חדרים) — מבוסס 259 עסקאות אמת
        apt_price = avg_sqm * apt_size if avg_sqm else 0
        equity     = apt_price * 0.25          # 25% הון עצמי
        mortgage   = apt_price * 0.75          # 75% משכנתא
        # החזר חודשי: ריבית 5% ל-25 שנה
        r = 0.05 / 12
        n = 25 * 12
        monthly = mortgage * (r * (1+r)**n) / ((1+r)**n - 1) if mortgage > 0 else 0
        min_income = monthly * 3  # כלל האצבע: משכנתא = עד 1/3 מהכנסה

        c["avg_price_sqm"] = avg_sqm
        c["apt_price"]  = apt_price
        c["equity"]     = equity
        c["monthly"]    = monthly
        c["min_income"] = min_income
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

    cities_json = json.dumps([
        {"city": c["city"], "sqm": round(c["avg_price_sqm"]), "prob": round(c["probability"], 2),
         "apartments": c["apartments"], "registered": c["registered"],
         "lotteries": c["lotteries"], "end_date": c["end_date"]}
        for c in sorted_cities
    ], ensure_ascii=False)

    rows_html = ""
    for i, c in enumerate(sorted_cities, 1):
        color = row_color(c["probability"])
        lot_label = f'{c["lotteries"]} הגרלות' if c["lotteries"] > 1 else "הגרלה 1"
        sqm_str = f'₪{c["avg_price_sqm"]:,.0f}' if c["avg_price_sqm"] else "—"
        rows_html += f"""
        <tr style="background:{color}" onclick="openModal({i-1})" class="clickable">
          <td style="font-weight:bold;color:#1a5276">{i}</td>
          <td style="font-weight:bold">{c['city']}</td>
          <td style="color:#666;font-size:12px">{lot_label}</td>
          <td>{c['apartments']:,}</td>
          <td>{c['registered']:,}</td>
          <td><strong>{c['probability']:.2f}%</strong></td>
          <td>{sqm_str}</td>
          <td>{c['end_date']}</td>
          <td style="text-align:center;color:#1a5276;font-size:16px">📊</td>
        </tr>"""

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
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:Arial,sans-serif;direction:rtl;background:#f0f4f8;padding:16px}}
    .header{{background:#1a5276;color:white;border-radius:12px;padding:16px 20px;margin-bottom:14px}}
    .header h1{{font-size:20px;margin-bottom:4px}}
    .update-time{{font-size:13px;opacity:.85;margin-top:8px}}
    .legend{{margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap}}
    .legend span{{padding:4px 12px;border-radius:20px;font-size:13px}}
    .green{{background:#d4edda}}.yellow{{background:#fff3cd}}.red{{background:#f8d7da}}
    .table-wrap{{overflow-x:auto;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
    table{{border-collapse:collapse;width:100%;background:white;min-width:500px}}
    th{{background:#1a5276;color:white;padding:10px 12px;text-align:right;font-size:13px}}
    td{{padding:9px 12px;border-bottom:1px solid #eee;font-size:13px}}
    tr:last-child td{{border-bottom:none}}
    .clickable{{cursor:pointer}}.clickable:hover{{filter:brightness(.93)}}
    .note{{margin-top:10px;color:#999;font-size:11px;text-align:center}}
    .count{{font-size:13px;opacity:.75;margin-top:2px}}

    /* overlay & modal */
    .overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:100;overflow-y:auto;padding:12px}}
    .overlay.open{{display:block}}
    .modal{{background:white;border-radius:14px;max-width:680px;margin:auto;padding:18px;position:relative}}
    .modal h2{{color:#1a5276;font-size:17px;margin-bottom:2px}}
    .close-btn{{position:absolute;top:12px;left:14px;font-size:22px;cursor:pointer;color:#888;border:none;background:none}}

    /* room tabs */
    .room-tabs{{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 0}}
    .rtab{{padding:5px 12px;border-radius:16px;border:2px solid #1a5276;color:#1a5276;
           cursor:pointer;font-size:12px;background:white}}
    .rtab.active{{background:#1a5276;color:white}}

    /* main tabs */
    .main-tabs{{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 10px;border-bottom:2px solid #e8f0fe;padding-bottom:8px}}
    .mtab{{padding:7px 12px;border-radius:8px 8px 0 0;border:none;color:#555;
           cursor:pointer;font-size:13px;background:none;font-weight:bold}}
    .mtab.active{{background:#1a5276;color:white;border-radius:8px}}
    .tab-pane{{display:none}}.tab-pane.active{{display:block}}

    /* section title */
    .stitle{{font-size:13px;color:#444;font-weight:bold;border-bottom:2px solid #e8f0fe;
             padding-bottom:4px;margin:14px 0 8px}}

    /* cards */
    .cards3{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}}
    .c3{{flex:1;min-width:100px;padding:10px;border-radius:10px;font-size:13px}}
    .c3 .cl{{font-size:11px;color:#666;margin-bottom:3px}}
    .c3 .cv{{font-size:15px;font-weight:bold}}
    .cb{{background:#e8f0fe}}.cr{{background:#fde8e8}}.cg{{background:#e8fde8}}

    /* track cards */
    .tgrid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
    @media(max-width:480px){{.tgrid{{grid-template-columns:repeat(2,1fr)}}}}
    .tc{{background:#f0f4f8;border-radius:8px;padding:9px;text-align:center}}
    .tc.tot{{background:#e8f0fe}}
    .tc .tn{{font-size:11px;color:#555;margin-bottom:3px}}
    .tc .tr{{font-size:11px;color:#999;margin-bottom:5px}}
    .tc .tp{{font-size:15px;font-weight:bold;color:#1a5276}}

    /* track explain */
    .texpl{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}}
    @media(max-width:480px){{.texpl{{grid-template-columns:1fr}}}}
    .tbox{{background:#f9f9f9;border-radius:10px;padding:11px;border:1px solid #e0e0e0}}
    .tbox h4{{font-size:12px;color:#1a5276;margin-bottom:7px}}
    .pc{{font-size:12px;margin:3px 0}}
    .pro{{color:#2e7d32}}.pro::before{{content:"✓ "}}
    .con{{color:#c62828}}.con::before{{content:"✗ "}}

    /* mix */
    .mix-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}
    @media(max-width:480px){{.mix-grid{{grid-template-columns:1fr}}}}
    .mcard{{background:white;border-radius:10px;padding:12px;border:2px solid #e0e0e0;cursor:pointer}}
    .mcard:hover,.mcard.sel{{border-color:#1a5276}}
    .mcard.sel{{background:#e8f0fe}}
    .mt{{font-size:13px;font-weight:bold;color:#1a5276;margin-bottom:6px}}
    .rbadge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:bold;margin-bottom:7px}}
    .rlow{{background:#e8fde8;color:#2e7d32}}.rmed{{background:#fff8e1;color:#f57f17}}.rhi{{background:#fde8e8;color:#c62828}}
    .mbars{{margin-bottom:8px}}
    .mbar-row{{display:flex;align-items:center;gap:6px;margin-bottom:3px;font-size:11px}}
    .mbar-bg{{flex:1;height:7px;background:#eee;border-radius:4px;overflow:hidden}}
    .mbar-fill{{height:100%;border-radius:4px}}
    .bp{{background:#ef5350}}.bf{{background:#42a5f5}}.ba{{background:#66bb6a}}
    .mstats{{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}}
    .ms{{flex:1;min-width:70px;background:#f5f5f5;border-radius:6px;padding:5px 7px;text-align:center}}
    .ms .msl{{font-size:10px;color:#888}}.ms .msv{{font-size:12px;font-weight:bold}}
    .mpc{{font-size:11px;margin-top:5px}}

    /* AI insights */
    .igrid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}}
    @media(max-width:480px){{.igrid{{grid-template-columns:1fr}}}}
    .icard{{border-radius:10px;padding:11px 13px;font-size:12px}}
    .icard .ii{{font-size:16px;margin-bottom:4px}}
    .icard .it{{color:#333;line-height:1.4}}
    .ig{{background:#e8fde8;border-right:4px solid #2e7d32}}
    .iy{{background:#fff8e1;border-right:4px solid #f9a825}}
    .ib{{background:#e8f0fe;border-right:4px solid #1565c0}}
    .ia{{background:#f3e5f5;border-right:4px solid #6a1b9a}}
    .sumcards{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}}
    @media(max-width:380px){{.sumcards{{grid-template-columns:1fr}}}}
    .scard-big{{border-radius:10px;padding:11px;text-align:center}}
    .scard-big .sl{{font-size:11px;color:#666;margin-bottom:3px}}
    .scard-big .sv{{font-size:16px;font-weight:bold}}
    .sg{{background:#e8fde8}}.sg .sv{{color:#2e7d32}}
    .sr{{background:#fde8e8}}.sr .sv{{color:#c62828}}
    .sbl{{background:#e8f0fe}}.sbl .sv{{color:#1565c0}}
    .sp{{background:#f3e5f5}}.sp .sv{{color:#6a1b9a}}

    /* AI conclusion */
    .aibox{{background:#f3e5f5;border-radius:10px;padding:12px;border-right:4px solid #6a1b9a;margin-top:12px}}
    .aibox h4{{color:#6a1b9a;margin-bottom:5px;font-size:13px}}
    .aibox p{{font-size:12px;color:#333;line-height:1.5}}
    .warn{{background:#fff8e1;border-right:4px solid #ffc107;padding:10px;border-radius:6px;font-size:12px;margin-top:10px;color:#555}}
  </style>
</head>
<body>
  <div class="header">
    <h1>🏠 הגרלות מחיר מטרה</h1>
    <p class="count">{len(sorted_cities)} ערים | {len(projects)} הגרלות פתוחות</p>
    <div class="update-time">עודכן: <time datetime="{now_iso}">{now_str}</time></div>
  </div>
  <div class="legend">
    <span class="green">ירוק &gt;5%</span>
    <span class="yellow">צהוב 2%–5%</span>
    <span class="red">אדום &lt;2%</span>
  </div>
  <p style="font-size:12px;color:#888;margin-bottom:8px">לחץ על שורה לניתוח פיננסי מלא 📊</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>#</th><th>עיר</th><th>הגרלות</th><th>דירות</th>
        <th>נרשמים</th><th>סיכוי</th><th>מחיר למ"ר</th><th>סיום</th><th></th></tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div id="install-bar" style="display:none;margin-top:14px;text-align:center">
    <button id="install-btn" style="padding:10px 24px;background:#1a5276;color:white;
      border:none;border-radius:24px;font-size:15px;cursor:pointer;">📲 הוסף למסך הבית</button>
  </div>
  <p id="ios-hint" style="display:none;margin-top:14px;text-align:center;font-size:13px;color:#555;">
    באייפון: לחץ ↑ שיתוף ← "הוסף למסך הבית"
  </p>
  <p class="note" style="margin-top:14px">מתעדכן אוטומטית כל שעה &nbsp;•&nbsp; לחץ שורה לניתוח מלא</p>
  <p class="note" style="margin-top:6px">Built by Lior Yehuda</p>

  <div class="overlay" id="overlay" onclick="closeModal(event)">
    <div class="modal" id="modal">
      <button class="close-btn" onclick="closeOverlay()">✕</button>
      <h2 id="m-title"></h2>
      <p id="m-sub" style="font-size:12px;color:#666;margin-top:2px"></p>
      <div id="m-rooms" class="room-tabs"></div>
      <div class="main-tabs" id="m-tabs"></div>
      <div id="m-body"></div>
    </div>
  </div>

  <script>
    const CITIES = {cities_json};
    const PRIME=0.06, SPREAD=-0.005, FIXED=0.055, ADJ5=0.048, YRS=25;
    const ROOM_SIZES={{3:83,4:107,5:126,6:140}};
    const AVG_SQM_NATIONAL = 16000;
    const MIXES = [
      {{name:'⚖️ מאוזן',       p:0.33,f:0.33,a:0.34, risk:'rmed',stars:'●●●○○',riskTxt:'בינוני',
        pros:['פיזור סיכון טוב','גמישות סבירה'],cons:['לא מיטבי בשום תרחיש']}},
      {{name:'🛡️ שמרני',       p:0.25,f:0.60,a:0.15, risk:'rlow',stars:'●●○○○',riskTxt:'נמוך',
        pros:['יציבות מקסימלית','בטוח לטווח ארוך'],cons:['החזר חודשי גבוה יותר','פחות גמישות']}},
      {{name:'🚀 אגרסיבי',     p:0.33,f:0.17,a:0.50, risk:'rhi', stars:'●●●●○',riskTxt:'גבוה',
        pros:['החזר נמוך כרגע','מנצל ריבית נמוכה'],cons:['חשיפה גבוהה לריבית','אי ודאות גבוהה']}},
      {{name:'🔒 יציבות מלאה', p:0.00,f:1.00,a:0.00, risk:'rlow',stars:'●○○○○',riskTxt:'מינימלי',
        pros:['אפס הפתעות','ידוע מראש עד הסוף'],cons:['ריבית גבוהה לאורך זמן','ללא גמישות']}}
    ];
    const MIX_AI = [
      'לרוכש ראשון עם הכנסה יציבה, התמהיל המאוזן מספק גמישות טובה יחד עם הגנה סבירה מפני עליית ריבית.',
      'מתאים למי שביטחון תקציבי הוא בראש סדר העדיפויות. אם צפויה ירידת ריבית בשנים הקרובות — אולי כדאי להישאר גמישים יותר.',
      'מתאים למי שמאמין שהריבית תרד. כרגע ההחזר נמוך, אך עליית ריבית של 2% תוסיף מאות שקלים בחודש.',
      'הבחירה הבטוחה ביותר. אתה יודע בדיוק כמה תשלם ב-2050. אך תשלם יותר ריבית בסך הכל.'
    ];

    let CC=null, CR=4, CT='costs', selMix=0;

    function monthly(p,r,y){{
      if(p<=0)return 0;const m=r/12,n=y*12;
      return p*(m*Math.pow(1+m,n))/(Math.pow(1+m,n)-1);
    }}
    function fmt(n){{return '₪'+Math.round(n).toLocaleString('he-IL');}}
    function calc(sqm,rooms,pd){{
      const sz=ROOM_SIZES[rooms]||107, price=sqm*sz;
      const eq=price*.25, mort=price*.75;
      const pr=PRIME+SPREAD+pd;
      const m1=monthly(mort/3,pr,YRS), m2=monthly(mort/3,FIXED,YRS), m3=monthly(mort/3,ADJ5,YRS);
      const tot=m1+m2+m3, paid=tot*YRS*12;
      return{{price,eq,mort,m1,m2,m3,tot,paid,int:paid-mort,inc:tot*3}};
    }}
    function calcMix(mort,mix,pd){{
      const pr=PRIME+SPREAD+pd;
      const m=monthly(mort*mix.p,pr,YRS)+monthly(mort*mix.f,FIXED,YRS)+monthly(mort*mix.a,ADJ5,YRS);
      return{{m,paid:m*YRS*12,int:m*YRS*12-mort}};
    }}

    function openModal(idx){{
      CC=CITIES[idx]; CR=4; CT='costs'; selMix=0;
      document.getElementById('m-title').textContent='📊 '+CC.city;
      document.getElementById('m-sub').textContent=
        `מחיר למ"ר: ${{fmt(CC.sqm)}} | סיכוי: ${{CC.prob}}% | ${{CC.lotteries}} הגרלות | סיום: ${{CC.end_date}}`;
      renderRooms(); renderTabs(); renderBody();
      document.getElementById('overlay').classList.add('open');
    }}

    function renderRooms(){{
      document.getElementById('m-rooms').innerHTML=
        Object.keys(ROOM_SIZES).map(r=>
          `<button class="rtab ${{+r===CR?'active':''}}" onclick="switchRoom(${{r}})">${{r}} חד׳ (${{ROOM_SIZES[r]}}מ"ר)</button>`
        ).join('');
    }}
    function renderTabs(){{
      const tabs=[['costs','💰 עלויות'],['mort','🏦 משכנתא'],['mix','🎛️ תמהיל'],['ai','🧠 תובנות AI']];
      document.getElementById('m-tabs').innerHTML=tabs.map(([id,lbl])=>
        `<button class="mtab ${{CT===id?'active':''}}" onclick="switchTab('${{id}}')">${{lbl}}</button>`
      ).join('');
    }}

    function renderBody(){{
      const d=calc(CC.sqm,CR,0), dU=calc(CC.sqm,CR,.01), dD=calc(CC.sqm,CR,-.01);
      const pRate=((PRIME+SPREAD)*100).toFixed(1), fRate=(FIXED*100).toFixed(1), aRate=(ADJ5*100).toFixed(1);
      const body = document.getElementById('m-body');

      if(CT==='costs') body.innerHTML=`
        <div class="stitle">💰 עלויות הדירה</div>
        <div class="cards3">
          <div class="c3 cb"><div class="cl">מחיר הדירה</div><div class="cv">${{fmt(d.price)}}</div></div>
          <div class="c3 cb"><div class="cl">הון עצמי (25%)</div><div class="cv">${{fmt(d.eq)}}</div></div>
          <div class="c3 cb"><div class="cl">משכנתא (75%)</div><div class="cv">${{fmt(d.mort)}}</div></div>
        </div>
        <div class="stitle">📈 תרחישי ריבית פריים</div>
        <div class="cards3">
          <div class="c3 cg"><div class="cl">פריים יורד 1%</div><div class="cv">${{fmt(dD.tot)}}/חודש</div>
            <div class="cl" style="margin-top:4px">חיסכון: ${{fmt(d.tot-dD.tot)}}/חודש</div></div>
          <div class="c3 cb"><div class="cl">ריבית נוכחית</div><div class="cv">${{fmt(d.tot)}}/חודש</div></div>
          <div class="c3 cr"><div class="cl">פריים עולה 1%</div><div class="cv">${{fmt(dU.tot)}}/חודש</div>
            <div class="cl" style="margin-top:4px">תוספת: ${{fmt(dU.tot-d.tot)}}/חודש</div></div>
        </div>
        <div class="stitle">📊 סה"כ לאורך 25 שנה</div>
        <div class="cards3">
          <div class="c3 cb"><div class="cl">סה"כ תשלומים</div><div class="cv">${{fmt(d.paid)}}</div></div>
          <div class="c3 cr"><div class="cl">מתוכם ריבית</div><div class="cv">${{fmt(d.int)}}</div></div>
          <div class="c3 cb"><div class="cl">הכנסה נדרשת</div><div class="cv">${{fmt(d.inc)}}</div></div>
        </div>
        <div class="warn">⚠️ הערכה בלבד. מומלץ להתייעץ עם יועץ משכנתאות.</div>`;

      else if(CT==='mort') body.innerHTML=`
        <div class="stitle">🏦 החזר חודשי — ריבית נוכחית</div>
        <div class="tgrid">
          <div class="tc"><div class="tn">פריים (משתנה)</div><div class="tr">${{pRate}}%</div><div class="tp">${{fmt(d.m1)}}</div></div>
          <div class="tc"><div class="tn">קל"צ (קבועה)</div><div class="tr">${{fRate}}%</div><div class="tp">${{fmt(d.m2)}}</div></div>
          <div class="tc"><div class="tn">משתנה כל 5Y</div><div class="tr">${{aRate}}%</div><div class="tp">${{fmt(d.m3)}}</div></div>
          <div class="tc tot"><div class="tn">סה"כ</div><div class="tr">1/3 כל מסלול</div><div class="tp">${{fmt(d.tot)}}</div></div>
        </div>
        <div class="stitle">📖 על המסלולים</div>
        <div class="texpl">
          <div class="tbox"><h4>🔴 פריים (משתנה)</h4>
            <div class="pc pro">גמישות — ניתן לפרוע ללא קנסות</div>
            <div class="pc pro">יורד כשהריבית יורדת</div>
            <div class="pc con">חשיפה לעליות ריבית</div>
            <div class="pc con">אי ודאות בהחזר החודשי</div>
          </div>
          <div class="tbox"><h4>🔵 קל"צ (קבועה)</h4>
            <div class="pc pro">יציבות — אותו החזר 25 שנה</div>
            <div class="pc pro">ביטחון תכנוני מלא</div>
            <div class="pc con">ריבית התחלתית גבוהה יחסית</div>
            <div class="pc con">קנסות פירעון מוקדם</div>
          </div>
          <div class="tbox"><h4>🟢 משתנה כל 5 שנים</h4>
            <div class="pc pro">ריבית נמוכה בהתחלה</div>
            <div class="pc pro">נחשפת לירידה כל 5 שנים</div>
            <div class="pc con">אי ודאות בעדכון כל 5 שנים</div>
            <div class="pc con">עלולה לעלות משמעותית</div>
          </div>
        </div>
        <div class="warn">⚠️ הערכה בלבד. מומלץ להתייעץ עם יועץ משכנתאות.</div>`;

      else if(CT==='mix'){{
        const mixCards=MIXES.map((mx,i)=>{{
          const mm=calcMix(d.mort,mx,0);
          const sel=i===selMix?'sel':'';
          return `<div class="mcard ${{sel}}" onclick="selectMix(${{i}})">
            <div class="mt">${{mx.name}}</div>
            <span class="rbadge ${{mx.risk}}">${{mx.stars}} ${{mx.riskTxt}}</span>
            <div class="mbars">
              <div class="mbar-row"><span style="width:65px">פריים ${{Math.round(mx.p*100)}}%</span>
                <div class="mbar-bg"><div class="mbar-fill bp" style="width:${{mx.p*100}}%"></div></div></div>
              <div class="mbar-row"><span style="width:65px">קל"צ ${{Math.round(mx.f*100)}}%</span>
                <div class="mbar-bg"><div class="mbar-fill bf" style="width:${{mx.f*100}}%"></div></div></div>
              <div class="mbar-row"><span style="width:65px">משתנה ${{Math.round(mx.a*100)}}%</span>
                <div class="mbar-bg"><div class="mbar-fill ba" style="width:${{mx.a*100}}%"></div></div></div>
            </div>
            <div class="mstats">
              <div class="ms"><div class="msl">החזר/חודש</div><div class="msv">${{fmt(mm.m)}}</div></div>
              <div class="ms"><div class="msl">סך ריבית</div><div class="msv">${{fmt(mm.int)}}</div></div>
            </div>
            <div class="mpc">
              ${{mx.pros.map(p=>`<div class="pc pro">${{p}}</div>`).join('')}}
              ${{mx.cons.map(c=>`<div class="pc con">${{c}}</div>`).join('')}}
            </div>
          </div>`;
        }}).join('');
        body.innerHTML=`
          <div class="stitle">🎛️ סימולציית תמהילים — ${{fmt(d.mort)}}</div>
          <div class="mix-grid">${{mixCards}}</div>
          <div class="aibox" id="aibox">
            <h4>🧠 מסקנת AI — ${{MIXES[selMix].name}}</h4>
            <p>${{MIX_AI[selMix]}}</p>
          </div>`;
      }}

      else if(CT==='ai'){{
        const sqm=CC.sqm, prob=CC.prob, mort=d.mort, tot=d.tot, inc=d.inc, interest=d.int;
        const insights=[];
        if(sqm>0 && sqm<AVG_SQM_NATIONAL)
          insights.push(['ig','🟢','מחיר למ"ר נמוך מהממוצע הארצי','₪'+sqm.toLocaleString()+' מול ממוצע ₪16,000+ לדירות חדשות — הנחה אמיתית.']);
        else if(sqm>=AVG_SQM_NATIONAL)
          insights.push(['iy','🟡','מחיר למ"ר קרוב לממוצע הארצי','בדוק את ההנחה האמיתית מול מחיר שוק חופשי באזור.']);
        if(prob>7)
          insights.push(['ig','🟢','סיכויי זכייה גבוהים יחסית',''+prob+'% — גבוה מהממוצע בהגרלות מחיר מטרה. שווה מאוד להירשם.']);
        else if(prob>3)
          insights.push(['ib','🔵','סיכויי זכייה בינוניים',''+prob+'% — סביר. כדאי לרשום כאחת מ-3 הערים שלך.']);
        else
          insights.push(['iy','🟡','סיכויי זכייה נמוכים',''+prob+'% — תחרות גבוהה. שקול ערים עם סיכוי טוב יותר.']);
        if(mort<800000)
          insights.push(['ig','🟢','גובה משכנתא נמוך יחסית',''+fmt(mort)+' — מקטין סיכון ומגדיל סיכוי לאישור בנקאי.']);
        if(tot/inc < 0.35)
          insights.push(['ig','🟢','יחס החזר נוח לאישור','החזר חודשי הוא '+Math.round(tot/inc*100)+'% מההכנסה הנדרשת — בנקים בדרך כלל מאשרים עד 40%.']);
        insights.push(['iy','🟡','חשיפה למסלולים משתנים','2/3 מהמשכנתא (פריים + משתנה 5Y) חשופים לשינויי ריבית בנק ישראל.']);
        if(interest/mort > 0.6)
          insights.push(['iy','🟡','סך הריבית גבוה לאורך זמן','תשלם '+fmt(interest)+' ריבית — '+Math.round(interest/mort*100)+'% מעל הקרן. פירעון מוקדם חוסך משמעותית.']);
        if(CC.lotteries>3)
          insights.push(['ib','🔵','ריבוי הגרלות בעיר — הזדמנות',''+CC.lotteries+' הגרלות מקבילות מגדילות מעשית את סיכויי הזכייה הכוללים בעיר.']);
        insights.push(['ia','🧠','מסקנת AI','שילוב '+(sqm<AVG_SQM_NATIONAL?'מחיר נמוך':'מחיר סביר')+', סיכוי '+(prob>5?'גבוה':prob>2?'בינוני':'נמוך')+' ומשכנתא '+(mort<900000?'נגישה':'משמעותית')+'. הסיכון העיקרי: חשיפה לריבית משתנה. המלצה: דון עם יועץ משכנתאות על תמהיל מתאים.']);

        const cards4=[
          ['sg','🟢 הזדמנות', sqm<AVG_SQM_NATIONAL?'מחיר נמוך':'בדוק שוק'],
          ['sbl','🔵 ביקוש',   CC.lotteries>3?'ריבוי הגרלות':'רגיל'],
          ['sr','🟡 סיכון',    'חשיפת ריבית'],
          ['sp','🧠 AI',       prob>5?'כדאי מאוד':prob>2?'כדאי':'שקול אחרות']
        ];
        body.innerHTML=`
          <div class="sumcards">
            ${{cards4.map(([cls,lbl,val])=>`<div class="scard-big ${{cls}}"><div class="sl">${{lbl}}</div><div class="sv">${{val}}</div></div>`).join('')}}
          </div>
          <div class="stitle">🧠 תובנות לפי נתוני העיר</div>
          <div class="igrid">
            ${{insights.map(([cls,icon,title,txt])=>`
              <div class="icard ${{cls}}"><div class="ii">${{icon}}</div>
              <div class="it"><strong>${{title}}</strong><br>${{txt}}</div></div>`).join('')}}
          </div>
          <div class="warn" style="margin-top:12px">⚠️ הערכה בלבד. מומלץ להתייעץ עם יועץ משכנתאות לפני קבלת החלטה.</div>`;
      }}
    }}

    function switchRoom(r){{CR=+r;renderRooms();renderBody();}}
    function switchTab(t){{CT=t;renderTabs();renderBody();}}
    function selectMix(i){{
      selMix=i;
      document.querySelectorAll('.mcard').forEach((c,j)=>c.classList.toggle('sel',j===i));
      document.getElementById('aibox').innerHTML=
        `<h4>🧠 מסקנת AI — ${{MIXES[i].name}}</h4><p>${{MIX_AI[i]}}</p>`;
    }}
    function closeModal(e){{if(e.target===document.getElementById('overlay'))closeOverlay();}}
    function closeOverlay(){{document.getElementById('overlay').classList.remove('open');}}
    document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeOverlay();}});

    document.querySelectorAll('time[datetime]').forEach(el=>{{
      const d=new Date(el.getAttribute('datetime')+'Z');
      el.textContent=d.toLocaleString('he-IL',{{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}});
    }});

    let dP;
    window.addEventListener('beforeinstallprompt',e=>{{e.preventDefault();dP=e;document.getElementById('install-bar').style.display='block';}});
    document.getElementById('install-btn')?.addEventListener('click',async()=>{{
      if(!dP)return;dP.prompt();const{{outcome}}=await dP.userChoice;dP=null;
      if(outcome==='accepted')document.getElementById('install-bar').style.display='none';
    }});
    window.addEventListener('appinstalled',()=>{{document.getElementById('install-bar').style.display='none';}});
    const isIOS=/iphone|ipad|ipod/i.test(navigator.userAgent);
    const isStandalone=window.matchMedia('(display-mode: standalone)').matches;
    if(isIOS&&!isStandalone)document.getElementById('ios-hint').style.display='block';
    if('serviceWorker' in navigator)navigator.serviceWorker.getRegistrations().then(r=>r.forEach(s=>s.unregister()));
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
