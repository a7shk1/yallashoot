# scripts/scrape_yallashoot_to_json.py
import os
import json
import datetime as dt
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BAGHDAD_TZ = ZoneInfo("Asia/Baghdad")
DEFAULT_URL = "https://www.yalla1shoot.com/matches-today_3/"

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "matches"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_PATH = OUT_DIR / "today.json"
DEBUG_HTML = OUT_DIR / "debug_page.html"
DEBUG_PNG = OUT_DIR / "debug_page.png"


def gradual_scroll(page, step=900, pause=0.25):
    last_h = 0
    while True:
        h = page.evaluate("() => document.body.scrollHeight")
        if h <= last_h:
            break
        for y in range(0, h, step):
            page.evaluate(f"window.scrollTo(0, {y});")
            time.sleep(pause)
        last_h = h


def scrape():
    url = os.environ.get("FORCE_URL") or DEFAULT_URL
    today = dt.datetime.now(BAGHDAD_TZ).date().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        ctx = browser.new_context(
            viewport={"width": 1366, "height": 864},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36",
            locale="ar",
            timezone_id="Asia/Baghdad",
        )

        # تقليل احتمالية كشف الـ webdriver
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = ctx.new_page()
        page.set_default_timeout(60000)

        print("[open]", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        # حاول تنتظر عنصر يدل إن المباريات موجودة
        try:
            page.wait_for_selector(".MT_Team.TM1 .TM_Name", timeout=60000)
        except PWTimeout:
            print("[warn] MT_Team not found within timeout")

        gradual_scroll(page)

        js = r"""
        () => {
          const abs = (u) => {
            if (!u) return "";
            try { return new URL(u, location.href).href; } catch { return u; }
          };

          function findRoot(el) {
            let n = el;
            while (n && n !== document.body) {
              const hasHome = n.querySelector?.('.MT_Team.TM1 .TM_Name');
              const hasAway = n.querySelector?.('.MT_Team.TM2 .TM_Name');
              const hasData = n.querySelector?.('.MT_Data');
              if (hasHome && hasAway && hasData) return n;
              n = n.parentElement;
            }
            return null;
          }

          // نجمع جذور الكروت من عناصر اسم الفريق (طريقة مقاومة لتغيّر الكلاسات الخارجية)
          const roots = new Map();
          document.querySelectorAll('.MT_Team.TM1 .TM_Name').forEach((nameEl) => {
            const r = findRoot(nameEl);
            if (r) roots.set(r, true);
          });

          const cards = [];
          for (const root of roots.keys()) {
            const qText = (sel) => {
              const el = root.querySelector(sel);
              return el ? el.textContent.trim() : "";
            };
            const qAttr = (sel, attr) => {
              const el = root.querySelector(sel);
              if (!el) return "";
              return el.getAttribute(attr) || el.getAttribute('data-' + attr) || "";
            };

            const home = qText('.MT_Team.TM1 .TM_Name');
            const away = qText('.MT_Team.TM2 .TM_Name');

            const homeLogo = abs(qAttr('.MT_Team.TM1 .TM_Logo img', 'src') || qAttr('.MT_Team.TM1 .TM_Logo img', 'data-src'));
            const awayLogo = abs(qAttr('.MT_Team.TM2 .TM_Logo img', 'src') || qAttr('.MT_Team.TM2 .TM_Logo img', 'data-src'));

            const time = qText('.MT_Data .MT_Time');
            const result = qText('.MT_Data .MT_Result');
            const status = qText('.MT_Data .MT_Stat');

            const infoLis = Array.from(root.querySelectorAll('.MT_Info li span')).map(x => x.textContent.trim());
            const channel = infoLis[0] || "";
            const commentator = infoLis[1] || "";
            const competition = infoLis[2] || "";

            if (home || away) {
              cards.push({
                home, away,
                home_logo: homeLogo, away_logo: awayLogo,
                time_local: time, result_text: result, status_text: status,
                channel, commentator, competition
              });
            }
          }

          return cards;
        }
        """

        cards = page.evaluate(js)

        # Debug إذا طلع فاضي: خزّن HTML + Screenshot حتى تشوف شنو الصفحة بالـ CI
        try:
            print("[debug] title:", page.title())
            print("[debug] MT_Team count:", page.locator(".MT_Team.TM1 .TM_Name").count())
        except Exception as e:
            print("[debug] cannot read title/count:", repr(e))

        if not cards:
            print("[warn] 0 cards found -> writing debug artifacts")
            try:
                DEBUG_HTML.write_text(page.content(), encoding="utf-8")
                page.screenshot(path=str(DEBUG_PNG), full_page=True)
                print("[debug] wrote:", str(DEBUG_HTML))
                print("[debug] wrote:", str(DEBUG_PNG))
            except Exception as e:
                print("[warn] failed to write debug artifacts:", repr(e))

        browser.close()

    print(f"[found] {len(cards)} cards")

    def normalize_status(ar_text: str) -> str:
        t = (ar_text or "").strip()
        if not t:
            return "NS"
        if "انتهت" in t or "نتهت" in t:
            return "FT"
        if "مباشر" in t or "الشوط" in t:
            return "LIVE"
        if "لم" in t and "تبدأ" in t:
            return "NS"
        return "NS"

    out = {
        "date": today,
        "source_url": url,
        "matches": [],
    }

    for c in cards:
        home = (c.get("home") or "").strip()
        away = (c.get("away") or "").strip()
        mid = f"{home[:12]}-{away[:12]}-{today}".replace(" ", "")

        out["matches"].append(
            {
                "id": mid,
                "home": home,
                "away": away,
                "home_logo": c.get("home_logo") or "",
                "away_logo": c.get("away_logo") or "",
                # هذا وقت بغداد لأن المتصفح مهيأ على Asia/Baghdad
                "time_baghdad": c.get("time_local") or "",
                "status": normalize_status(c.get("status_text") or ""),
                "status_text": c.get("status_text") or "",
                "result_text": c.get("result_text") or "",
                "channel": (c.get("channel") or None),
                "commentator": (c.get("commentator") or None),
                "competition": (c.get("competition") or None),
                "_source": "yalla1shoot",
            }
        )

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[write] {OUT_PATH} with {len(out['matches'])} matches.")


if __name__ == "__main__":
    scrape()
