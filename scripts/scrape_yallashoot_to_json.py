# scripts/scrape_yallashoot_to_json.py
import os, json, datetime as dt, time
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


def normalize_status(ar_text: str) -> str:
    t = (ar_text or "").strip()
    if not t:
        return "NS"
    # أمثلة بالموقع: "لم تبدأ بعد", "لم تبدأ", "مباشر", "الشوط ..."
    if "انتهت" in t or "نتهت" in t:
        return "FT"
    if "مباشر" in t or "الشوط" in t or "جارية" in t:
        return "LIVE"
    if "لم" in t and ("تبدأ" in t or "يبدا" in t):
        return "NS"
    return "NS"


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
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        page = ctx.new_page()
        page.set_default_timeout(60000)

        print("[open]", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        # ✅ انتظر الكروت بالهيكل الجديد أولاً
        found_mode = "new"
        try:
            page.wait_for_selector("#ayala-today .ay_4da65fab", timeout=25000)
        except PWTimeout:
            found_mode = "old"
            try:
                page.wait_for_selector(".AY_Inner", timeout=15000)
            except PWTimeout:
                pass

        gradual_scroll(page)

        # JS يطلع البيانات من الموديل الجديد (حسب ملف HTML عندك)
        js_new = r"""
        () => {
          const abs = (u) => {
            if (!u) return "";
            try { return new URL(u, location.href).href; } catch { return u; }
          };

          const cards = [];
          const nodes = document.querySelectorAll('#ayala-today .ay_4da65fab');
          for (const root of nodes) {
            const q = (sel) => root.querySelector(sel);
            const txt = (sel) => {
              const el = q(sel);
              return el ? el.textContent.trim() : "";
            };
            const attr = (sel, name) => {
              const el = q(sel);
              if (!el) return "";
              return el.getAttribute(name) || "";
            };

            const home = txt('.TM1 .ay_89345e16');
            const away = txt('.TM2 .ay_89345e16');

            // الصور Lazy: data-src
            const homeLogo = abs(attr('.TM1 .ay_00bd1448 img', 'data-src') || attr('.TM1 .ay_00bd1448 img', 'src'));
            const awayLogo = abs(attr('.TM2 .ay_00bd1448 img', 'data-src') || attr('.TM2 .ay_00bd1448 img', 'src'));

            const time = txt('.ay_2b054044 .ay_5b70f280');      // مثل 14:30
            const status = txt('.ay_2b054044 .ay_40296633');    // مثل لم تبدأ بعد

            const info = Array.from(root.querySelectorAll('.ay_7bd00217 ul li span')).map(x => x.textContent.trim());
            const channel = info[0] || "";
            const commentator = info[1] || "";
            const competition = info[2] || "";

            const matchUrl = abs(attr('a[href*="/matches/"]', 'href'));

            if (home || away) {
              cards.push({
                home, away,
                home_logo: homeLogo, away_logo: awayLogo,
                time_local: time,
                status_text: status,
                result_text: "", // بالموديل الجديد النتيجة ضمن spans RS-goals لو تريدها نضيفها
                channel, commentator, competition,
                match_url: matchUrl
              });
            }
          }
          return cards;
        }
        """

        # JS قديم (احتياط)
        js_old = r"""
        () => {
          const cards = [];
          document.querySelectorAll('.AY_Inner').forEach((inner) => {
            const root = inner.parentElement || inner;

            const qText = (sel) => {
              const el = root.querySelector(sel);
              return el ? el.textContent.trim() : "";
            };
            const qAttr = (sel, attr) => {
              const el = root.querySelector(sel);
              if (!el) return "";
              return el.getAttribute(attr) || el.getAttribute('data-' + attr) || "";
            };
            const abs = (u) => {
              if (!u) return "";
              try { return new URL(u, location.href).href; } catch { return u; }
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

            cards.push({
              home, away, home_logo: homeLogo, away_logo: awayLogo,
              time_local: time, result_text: result, status_text: status,
              channel, commentator, competition,
              match_url: ""
            });
          });
          return cards;
        }
        """

        cards = page.evaluate(js_new if found_mode == "new" else js_old)

        print("[debug] mode:", found_mode)
        print("[debug] title:", page.title())
        print("[debug] new cards count:", page.locator("#ayala-today .ay_4da65fab").count())

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

    out = {"date": today, "source_url": url, "matches": []}

    for c in cards:
        home = (c.get("home") or "").strip()
        away = (c.get("away") or "").strip()
        mid = f"{home[:12]}-{away[:12]}-{today}".replace(" ", "")

        status_text = (c.get("status_text") or "").strip()

        out["matches"].append({
            "id": mid,
            "home": home,
            "away": away,
            "home_logo": c.get("home_logo") or "",
            "away_logo": c.get("away_logo") or "",
            # الصفحة كاتبة "بتوقيت الرياض" ويناير نفس توقيت بغداد (+3) فهنا يكفي نخزنه مباشرة
            "time_baghdad": c.get("time_local") or "",
            "status": normalize_status(status_text),
            "status_text": status_text,
            "result_text": (c.get("result_text") or "").strip(),
            "channel": (c.get("channel") or "").strip() or None,
            "commentator": (c.get("commentator") or "").strip() or None,
            "competition": (c.get("competition") or "").strip() or None,
            "match_url": (c.get("match_url") or "").strip() or None,
            "_source": "yalla1shoot",
        })

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[write] {OUT_PATH} with {len(out['matches'])} matches.")


if __name__ == "__main__":
    scrape()
