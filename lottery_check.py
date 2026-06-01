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
        apt_size = 80  # מ"ר ממוצע לדירת מחיר מטרה
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

    # הכן נתוני ערים כ-JSON להזרקה ל-JS
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
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: Arial, sans-serif; direction: rtl; background: #f0f4f8; padding: 16px; }}
    .header {{ background: #1a5276; color: white; border-radius: 12px; padding: 16px 20px; margin-bottom: 14px; }}
    .header h1 {{ font-size: 20px; margin-bottom: 4px; }}
    .update-time {{ font-size: 13px; opacity: 0.85; margin-top: 8px; }}
    .legend {{ margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; }}
    .legend span {{ padding: 4px 12px; border-radius: 20px; font-size: 13px; }}
    .green  {{ background: #d4edda; }} .yellow {{ background: #fff3cd; }} .red {{ background: #f8d7da; }}
    .table-wrap {{ overflow-x: auto; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,.1); }}
    table {{ border-collapse: collapse; width: 100%; background: white; min-width: 500px; }}
    th {{ background: #1a5276; color: white; padding: 10px 12px; text-align: right; font-size: 13px; }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #eee; font-size: 13px; }}
    tr:last-child td {{ border-bottom: none; }}
    .clickable {{ cursor: pointer; }} .clickable:hover {{ filter: brightness(0.93); }}
    .note {{ margin-top: 10px; color: #999; font-size: 11px; text-align: center; }}
    .count {{ font-size: 13px; opacity: 0.75; margin-top: 2px; }}

    /* Modal */
    .overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:100; overflow-y:auto; padding:16px; }}
    .overlay.open {{ display:block; }}
    .modal {{ background:white; border-radius:14px; max-width:700px; margin:auto; padding:20px; position:relative; }}
    .modal h2 {{ color:#1a5276; font-size:18px; margin-bottom:4px; }}
    .modal .close {{ position:absolute; top:14px; left:16px; font-size:22px; cursor:pointer; color:#888; border:none; background:none; }}
    .modal-section {{ margin-top:18px; }}
    .modal-section h3 {{ font-size:14px; color:#444; margin-bottom:8px; border-bottom:2px solid #e8f0fe; padding-bottom:4px; }}
    .tabs {{ display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }}
    .tab {{ padding:6px 16px; border-radius:20px; border:2px solid #1a5276; color:#1a5276;
            cursor:pointer; font-size:13px; background:white; }}
    .tab.active {{ background:#1a5276; color:white; }}
    .mtable {{ width:100%; border-collapse:collapse; font-size:13px; }}
    .mtable th {{ background:#f0f4f8; color:#333; padding:8px 10px; text-align:right; }}
    .mtable td {{ padding:8px 10px; border-bottom:1px solid #f0f0f0; }}
    .mtable tr:last-child td {{ border:none; font-weight:bold; background:#e8f0fe; }}
    .scenario-box {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:8px; }}
    .scard {{ flex:1; min-width:130px; padding:10px 14px; border-radius:10px; font-size:13px; }}
    .scard .label {{ font-size:11px; color:#666; margin-bottom:4px; }}
    .scard .val {{ font-size:16px; font-weight:bold; }}
    .scard.base {{ background:#e8f0fe; }}
    .scard.up {{ background:#fde8e8; }}
    .scard.down {{ background:#e8fde8; }}
    .warn {{ background:#fff8e1; border-right:4px solid #ffc107; padding:10px 14px;
             border-radius:6px; font-size:13px; margin-top:10px; color:#555; }}
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
        <tr>
          <th>#</th><th>עיר</th><th>הגרלות</th><th>דירות</th>
          <th>נרשמים</th><th>סיכוי</th><th>מחיר למ"ר</th><th>סיום הרשמה</th><th></th>
        </tr>
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
  <p class="note" style="margin-top:14px">מתעדכן אוטומטית כל שעה &nbsp;•&nbsp; לחץ שורה לניתוח פיננסי</p>
  <p class="note" style="margin-top:6px">Built by Lior Yehuda</p>

  <!-- Modal -->
  <div class="overlay" id="overlay" onclick="closeModal(event)">
    <div class="modal" id="modal">
      <button class="close" onclick="closeOverlay()">✕</button>
      <h2 id="modal-title"></h2>
      <p id="modal-sub" style="font-size:13px;color:#666;margin-top:2px"></p>
      <div id="modal-tabs" class="tabs"></div>
      <div id="modal-body"></div>
    </div>
  </div>

  <script>
    const CITIES = {cities_json};

    // ריביות עדכניות (יוני 2025)
    const PRIME = 0.06;          // ריבית פריים שנתית
    const PRIME_SPREAD = -0.005; // מרווח פריים מינוס חצי אחוז
    const FIXED = 0.055;         // קל"צ
    const ADJUST5 = 0.048;       // משתנה כל 5 שנים
    const YEARS = 25;
    const ROOM_SIZES = {{3: 65, 4: 85, 5: 110}};

    function monthly(principal, annualRate, years) {{
      if (principal <= 0) return 0;
      const r = annualRate / 12, n = years * 12;
      return principal * (r * Math.pow(1+r, n)) / (Math.pow(1+r, n) - 1);
    }}

    function fmt(n) {{
      return '₪' + Math.round(n).toLocaleString('he-IL');
    }}

    function calcCity(sqmPrice, rooms, primeDelta) {{
      const size = ROOM_SIZES[rooms];
      const price = sqmPrice * size;
      const equity = price * 0.25;
      const mortTotal = price * 0.75;
      const third = mortTotal / 3;

      const primeRate = PRIME + PRIME_SPREAD + primeDelta;
      const m1 = monthly(third, primeRate, YEARS);   // פריים
      const m2 = monthly(third, FIXED,    YEARS);    // קל"צ
      const m3 = monthly(third, ADJUST5,  YEARS);    // משתנה 5Y
      const totalMonthly = m1 + m2 + m3;
      const totalPaid = totalMonthly * YEARS * 12;
      const interest = totalPaid - mortTotal;

      return {{ price, equity, mortTotal, m1, m2, m3, totalMonthly, totalPaid, interest,
               minIncome: totalMonthly * 3 }};
    }}

    let currentCity = null;
    let currentRooms = 4;

    function openModal(idx) {{
      currentCity = CITIES[idx];
      currentRooms = 4;
      renderModal();
      document.getElementById('overlay').classList.add('open');
    }}

    function renderModal() {{
      const c = currentCity;
      document.getElementById('modal-title').textContent = '📊 ' + c.city;
      document.getElementById('modal-sub').textContent =
        `מחיר למ"ר: ${{fmt(c.sqm)}} | סיכוי: ${{c.prob}}% | ${{c.lotteries}} הגרלות | סיום: ${{c.end_date}}`;

      // טאבים
      const tabs = document.getElementById('modal-tabs');
      tabs.innerHTML = [3,4,5].map(r =>
        `<button class="tab ${{r===currentRooms?'active':''}}" onclick="switchRooms(${{r}})">${{r}} חדרים (${{ROOM_SIZES[r]}}מ"ר)</button>`
      ).join('');

      const d = calcCity(c.sqm, currentRooms, 0);
      const dUp = calcCity(c.sqm, currentRooms, 0.01);
      const dDown = calcCity(c.sqm, currentRooms, -0.01);

      document.getElementById('modal-body').innerHTML = `
        <div class="modal-section">
          <h3>💰 עלויות הדירה (${{currentRooms}} חדרים, ${{ROOM_SIZES[currentRooms]}}מ"ר)</h3>
          <div class="scenario-box">
            <div class="scard base"><div class="label">מחיר הדירה</div><div class="val">${{fmt(d.price)}}</div></div>
            <div class="scard base"><div class="label">הון עצמי נדרש (25%)</div><div class="val">${{fmt(d.equity)}}</div></div>
            <div class="scard base"><div class="label">משכנתא (75%)</div><div class="val">${{fmt(d.mortTotal)}}</div></div>
          </div>
        </div>

        <div class="modal-section">
          <h3>🏦 פירוט החזר חודשי (25 שנה) — ריבית נוכחית</h3>
          <table class="mtable">
            <tr><th>מסלול</th><th>חלק</th><th>ריבית</th><th>החזר חודשי</th></tr>
            <tr><td>פריים (משתנה)</td><td>${{fmt(d.mortTotal/3)}}</td>
                <td>${{((PRIME+PRIME_SPREAD)*100).toFixed(1)}}%</td><td>${{fmt(d.m1)}}</td></tr>
            <tr><td>קל"צ (קבועה)</td><td>${{fmt(d.mortTotal/3)}}</td>
                <td>${{(FIXED*100).toFixed(1)}}%</td><td>${{fmt(d.m2)}}</td></tr>
            <tr><td>משתנה כל 5 שנים</td><td>${{fmt(d.mortTotal/3)}}</td>
                <td>${{(ADJUST5*100).toFixed(1)}}%</td><td>${{fmt(d.m3)}}</td></tr>
            <tr><td colspan="3">סה"כ החזר חודשי</td><td>${{fmt(d.totalMonthly)}}</td></tr>
          </table>
        </div>

        <div class="modal-section">
          <h3>📈 תרחישי ריבית פריים</h3>
          <div class="scenario-box">
            <div class="scard down">
              <div class="label">פריים יורד 1%</div>
              <div class="val">${{fmt(dDown.totalMonthly)}}/חודש</div>
              <div class="label" style="margin-top:4px">חיסכון: ${{fmt(d.totalMonthly-dDown.totalMonthly)}}/חודש</div>
            </div>
            <div class="scard base">
              <div class="label">ריבית נוכחית</div>
              <div class="val">${{fmt(d.totalMonthly)}}/חודש</div>
            </div>
            <div class="scard up">
              <div class="label">פריים עולה 1%</div>
              <div class="val">${{fmt(dUp.totalMonthly)}}/חודש</div>
              <div class="label" style="margin-top:4px">תוספת: ${{fmt(dUp.totalMonthly-d.totalMonthly)}}/חודש</div>
            </div>
          </div>
        </div>

        <div class="modal-section">
          <h3>📊 סה"כ לאורך 25 שנה</h3>
          <div class="scenario-box">
            <div class="scard base"><div class="label">סה"כ תשלומים</div><div class="val">${{fmt(d.totalPaid)}}</div></div>
            <div class="scard up"><div class="label">מתוכם ריבית</div><div class="val">${{fmt(d.interest)}}</div></div>
            <div class="scard base"><div class="label">הכנסה חודשית נדרשת</div><div class="val">${{fmt(d.minIncome)}}</div></div>
          </div>
        </div>

        <div class="warn" style="margin-top:16px">
          ⚠️ <strong>שים לב:</strong> אלו הערכות בלבד. מסלול הפריים משתנה עם ריבית בנק ישראל.
          מומלץ לבצע סימולציה מול יועץ משכנתאות לפני קבלת החלטה.
        </div>`;
    }}

    function switchRooms(r) {{
      currentRooms = r;
      renderModal();
    }}

    function closeModal(e) {{
      if (e.target === document.getElementById('overlay')) closeOverlay();
    }}
    function closeOverlay() {{
      document.getElementById('overlay').classList.remove('open');
    }}
    document.addEventListener('keydown', e => {{ if(e.key==='Escape') closeOverlay(); }});

    // המר שעת UTC לשעון מקומי
    document.querySelectorAll('time[datetime]').forEach(el => {{
      const d = new Date(el.getAttribute('datetime') + 'Z');
      el.textContent = d.toLocaleString('he-IL', {{
        day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit'
      }});
    }});

    // PWA install
    let deferredPrompt;
    window.addEventListener('beforeinstallprompt', e => {{
      e.preventDefault(); deferredPrompt = e;
      document.getElementById('install-bar').style.display = 'block';
    }});
    document.getElementById('install-btn')?.addEventListener('click', async () => {{
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const {{ outcome }} = await deferredPrompt.userChoice;
      deferredPrompt = null;
      if (outcome === 'accepted') document.getElementById('install-bar').style.display = 'none';
    }});
    window.addEventListener('appinstalled', () => {{
      document.getElementById('install-bar').style.display = 'none';
    }});
    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches;
    if (isIOS && !isStandalone) document.getElementById('ios-hint').style.display = 'block';

    // ביטול SW ישנים
    if ('serviceWorker' in navigator) {{
      navigator.serviceWorker.getRegistrations().then(regs => regs.forEach(r => r.unregister()));
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
