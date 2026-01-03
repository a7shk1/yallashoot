# scripts/scrape_yallashoot_to_json.py
import os
import json
import re
import datetime as dt
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BAGHDAD_TZ = ZoneInfo("Asia/Baghdad")

DEFAULT_URL = "https://www.yalla1shoot.com/matches-today_3/"
DEFAULT_FALLBACK_URL = "https://yalla-shoot.im/matches-today/"

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
    if "انتهت" in t or "نتهت" in t:
        return "FT"
    if "مباشر" in t or "الشوط" in t:
        return "LIVE"
    if "لم" in t and "تبدأ" in t:
        return "NS"
    return "NS"


def scrape_primary_playwright(url: str):
    """
    يرجع list[dict] بنفس مفاتيح الكروت:
      home, away, home_logo, away_logo, time_local, result_text, status_text, channel, commentator, competition
    """
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

        # Debug info
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
        return cards


def scrape_fallback_static(fallback_url: str):
    """
    يسحب من صفحة HTML ثابتة (مثل yalla-shoot.im/matches-today)
    ويستخرج مباريات من نصوص الروابط بالشكل:
      "فريق1 03:30 م فريق2"
    """
    print("[fallback] open", fallback_url)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36",
        "Accept-Language": "ar,en;q=0.8",
    }
    r = requests.get(fallback_url, headers=headers, timeout=40)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # نمط الوقت العربي: 03:30 م أو 11:05 ص
    time_re = re.compile(r"^(?P<home>.+?)\s+(?P<h>\d{1,2}:\d{2})\s*(?P<ampm>[صم])\s+(?P<away>.+)$")

    cards = []
    current_competition = ""

    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        text = " ".join(a.get_text(" ", strip=True).split())
        if not text:
            continue

        # تحديث اسم البطولة إذا الرابط يخص صفحة بطولة
        if "/championship/" in href:
            current_competition = text
            continue

        m = time_re.match(text)
        if not m:
            continue

        home = m.group("home").strip()
        away = m.group("away").strip()
        time_local = f"{m.group('h')} {m.group('ampm')}".strip()

        cards.append(
            {
                "home": home,
                "away": away,
                "home_logo": "",
                "away_logo": "",
                "time_local": time_local,
                "result_text": "",
                "status_text": "لم تبدأ" if time_local else "",
                "channel": "",
                "commentator": "",
                "competition": current_competition,
            }
        )

    print(f"[fallback] found {len(cards)} matches")
    return cards


def main():
    url = os.environ.get("FORCE_URL") or DEFAULT_URL
    fallback_url = os.environ.get("FALLBACK_URL") or DEFAULT_FALLBACK_URL
    today = dt.datetime.now(BAGHDAD_TZ).date().isoformat()

    cards = scrape_primary_playwright(url)

    # إذا CI محجوب وماكو DOM، انتقل للفولباك
    if not cards:
        try:
            cards = scrape_fallback_static(fallback_url)
            # بدّل مصدر الرابط بالـ JSON حتى يكون واضح منين جاي
            source_url = fallback_url
            source_name = "yalla-shoot.im (fallback)"
        except Exception as e:
            print("[fallback] failed:", repr(e))
            cards = []
            source_url = url
            source_name = "yalla1shoot"
    else:
        source_url = url
        source_name = "yalla1shoot"

    out = {"date": today, "source_url": source_url, "matches": []}

    for c in cards:
        home = (c.get("home") or "").strip()
        away = (c.get("away") or "").strip()
        mid = f"{home[:12]}-{away[:12]}-{today}".replace(" ", "")

        status_text = (c.get("status_text") or "").strip()
        out["matches"].append(
            {
                "id": mid,
                "home": home,
                "away": away,
                "home_logo": c.get("home_logo") or "",
                "away_logo": c.get("away_logo") or "",
                "time_baghdad": c.get("time_local") or "",
                "status": normalize_status(status_text),
                "status_text": status_text,
                "result_text": (c.get("result_text") or "").strip(),
                "channel": (c.get("channel") or None) or None,
                "commentator": (c.get("commentator") or None) or None,
                "competition": (c.get("competition") or None) or None,
                "_source": source_name,
            }
        )

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[write] {OUT_PATH} with {len(out['matches'])} matches.")


if __name__ == "__main__":
    main()
