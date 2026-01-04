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

def normalize_status(ar_text: str) -> str:
    t = (ar_text or "").strip()
    if not t:
        return "NS"
    if "انتهت" in t or "نتهت" in t:
        return "FT"
    if "مباشر" in t or "الشوط" in t or "حي" in t:
        return "LIVE"
    if "لم" in t and ("تبدأ" in t or "تبد" in t):
        return "NS"
    return "NS"

def scrape():
    url = os.environ.get("FORCE_URL") or DEFAULT_URL
    today = dt.datetime.now(BAGHDAD_TZ).date().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 864},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36",
            locale="ar",
            timezone_id="Asia/Baghdad",
        )
        page = ctx.new_page()
        page.set_default_timeout(60000)

        print("[open]", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        # أهم شي: انتظر كونتينر مباريات اليوم + وجود TM1/TM2
        try:
            page.wait_for_selector("#ayala-today", timeout=25000)
            page.wait_for_selector("#ayala-today .TM1, #ayala-today .TM2", timeout=25000)
        except PWTimeout:
            print("[warn] ayala-today/TM1/TM2 not found within timeout")

        gradual_scroll(page)

        js = r"""
        () => {
          const anchors = Array.from(document.querySelectorAll('#ayala-today a[href*="/matches/"]'));
          const seen = new Set();
          const out = [];

          function findCardRoot(a) {
            let el = a;
            while (el && el !== document.body) {
              if (el.querySelector && el.querySelector('.TM1') && el.querySelector('.TM2')) return el;
              el = el.parentElement;
            }
            return null;
          }

          function pickName(teamBox) {
            if (!teamBox) return "";
            // غالباً الاسم آخر div داخل TM1/TM2
            const byOld = teamBox.querySelector('.TM_Name');
            if (byOld) return byOld.textContent.trim();

            const last = teamBox.querySelector(':scope > div:last-child, :scope > span:last-child');
            if (last && last.textContent) return last.textContent.trim();

            return teamBox.textContent.trim();
          }

          function pickLogo(teamBox) {
            if (!teamBox) return "";
            const img = teamBox.querySelector('img');
            if (!img) return "";
            // الشعارات الحقيقية عادة بـ data-src
            return img.getAttribute('data-src') ||
                   img.getAttribute('data-lazy-src') ||
                   img.getAttribute('data-original') ||
                   img.getAttribute('src') || "";
          }

          function pickTime(root) {
            const mt = root.querySelector('.MT_Time');
            if (mt) return mt.textContent.trim();

            const nodes = Array.from(root.querySelectorAll('span,div'));
            const found = nodes.find(n => /^\s*\d{1,2}:\d{2}\s*([صم]\s*)?$/.test((n.textContent || '').trim()));
            return found ? found.textContent.trim() : "";
          }

          function pickStatus(root) {
            const st = root.querySelector('.MT_Stat');
            if (st) return st.textContent.trim();

            const nodes = Array.from(root.querySelectorAll('div,span'));
            const found = nodes.find(n => /(لم تبدأ|مباشر|انتهت|الشوط)/.test(n.textContent || ""));
            return found ? found.textContent.trim() : "";
          }

          function pickResult(root) {
            const r = root.querySelector('.MT_Result');
            if (r) return r.textContent.trim();

            // نمط: 0 - 0 (موجود كسـ spans)
            const parts = Array.from(root.querySelectorAll('span'))
              .map(s => (s.textContent || '').trim())
              .filter(Boolean);
            const di = parts.indexOf('-');
            if (di > 0 && di < parts.length - 1) {
              const a = parts[di - 1];
              const b = parts[di + 1];
              if (/^\d+$/.test(a) && /^\d+$/.test(b)) return `${a}-${b}`;
            }
            return "";
          }

          for (const a of anchors) {
            const root = findCardRoot(a);
            if (!root) continue;
            if (seen.has(root)) continue;
            seen.add(root);

            const tm1 = root.querySelector('.TM1');
            const tm2 = root.querySelector('.TM2');

            const infoSpans = Array.from(root.querySelectorAll('ul li span'))
              .map(s => (s.textContent || '').trim())
              .filter(Boolean);

            out.push({
              home: pickName(tm1),
              away: pickName(tm2),
              home_logo: pickLogo(tm1),
              away_logo: pickLogo(tm2),
              time_local: pickTime(root),
              result_text: pickResult(root),
              status_text: pickStatus(root),
              channel: infoSpans[0] || "",
              commentator: infoSpans[1] || "",
              competition: infoSpans[2] || "",
              match_url: a.href || ""
            });
          }

          return out;
        }
        """

        cards = page.evaluate(js)

        # إذا فشل وجاب صفر، خزّن artifacts للتشخيص
        if not cards:
            print("[warn] 0 cards found -> writing debug artifacts")
            try:
                DEBUG_HTML.write_text(page.content(), encoding="utf-8")
                print("[debug] wrote:", str(DEBUG_HTML))
            except Exception as e:
                print("[debug] failed to write html:", e)

            try:
                page.screenshot(path=str(DEBUG_PNG), full_page=True)
                print("[debug] wrote:", str(DEBUG_PNG))
            except Exception as e:
                print("[debug] failed to write png:", e)

        browser.close()

    print(f"[found] {len(cards)} cards")

    # نظّف الشعارات: إذا طلع data:image (placeholder) اعتبره فاضي
    def clean_logo(u: str) -> str:
        u = (u or "").strip()
        if u.startswith("data:image"):
            return ""
        return u

    out = {
        "date": today,
        "source_url": url,
        "matches": []
    }

    for c in cards:
        home = (c.get("home") or "").strip()
        away = (c.get("away") or "").strip()
        if not home or not away:
            continue

        mid = f"{home[:12]}-{away[:12]}-{today}".replace(" ", "")
        out["matches"].append({
            "id": mid,
            "home": home,
            "away": away,
            "home_logo": clean_logo(c.get("home_logo", "")),
            "away_logo": clean_logo(c.get("away_logo", "")),
            "time_baghdad": (c.get("time_local") or "").strip(),
            "status": normalize_status(c.get("status_text", "")),
            "status_text": (c.get("status_text") or "").strip(),
            "result_text": (c.get("result_text") or "").strip(),
            "channel": (c.get("channel") or "").strip() or None,
            "commentator": (c.get("commentator") or "").strip() or None,
            "competition": (c.get("competition") or "").strip() or None,
            "match_url": (c.get("match_url") or "").strip() or None,
            "_source": "yalla1shoot"
        })

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[write] {OUT_PATH} with {len(out['matches'])} matches.")

if __name__ == "__main__":
    scrape()
