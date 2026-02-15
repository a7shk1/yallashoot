# scripts/scrape_yallashoot_to_json.py
import os, json, datetime as dt, time
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BAGHDAD_TZ = ZoneInfo("Asia/Baghdad")
DEFAULT_URL = "https://www.yalla1shoot.com/matches-today_5/"

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


def status_to_ar(root_text: str) -> str:
    t = (root_text or "").strip()

    # انتهت
    if ("انتهت" in t) or ("نتهت" in t):
        return "انتهت"

    # مباشر (أي شيء يدل على اللعب: مباشر/الشوط/استراحة…)
    live_markers = ["مباشر", "الشوط", "استراحة", "بين الشوطين", "جارية", "الوقت الإضافي", "ركلات الترجيح"]
    if any(x in t for x in live_markers):
        return "مباشر"

    # الافتراضي
    return "لم تبدأ بعد"


def status_code(ar_status: str) -> str:
    if ar_status == "انتهت":
        return "FT"
    if ar_status == "مباشر":
        return "LIVE"
    return "NS"


def clean_logo(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("data:image"):
        return ""
    return u


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
                "--ignore-certificate-errors",  # ضفتلك هاي
            ],
        )
        # وضفتلك ignore_https_errors=True هنا
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 864},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36",
            locale="ar",
            timezone_id="Asia/Baghdad",
            ignore_https_errors=True,
        )
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        page = ctx.new_page()
        page.set_default_timeout(60000)

        print("[open]", url)
        # حتى لو صار error بالشهادة، الـ ignore الفوك راح يعالجه، بس للاحتياط نخلي try هنا هم
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[warn] goto error (might be ignored): {e}")

        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        # حاول تنتظر كونتينر اليوم (إذا موجود)
        try:
            page.wait_for_selector("#ayala-today", timeout=25000)
        except PWTimeout:
            print("[warn] #ayala-today not found within timeout")

        gradual_scroll(page)

        js = r"""
        () => {
          const abs = (u) => {
            if (!u) return "";
            try { return new URL(u, location.href).href; } catch { return u; }
          };

          // نلقط روابط المباريات ونطلع منها "جذر" الكرت اعتمادًا على وجود TM1 و TM2
          const anchors = Array.from(document.querySelectorAll('a[href*="/matches/"]'));
          const roots = [];
          const seen = new Set();

          function findRoot(a) {
            let el = a;
            while (el && el !== document.body) {
              if (el.querySelector && el.querySelector('.TM1') && el.querySelector('.TM2')) return el;
              el = el.parentElement;
            }
            return null;
          }

          for (const a of anchors) {
            const r = findRoot(a);
            if (!r) continue;
            if (seen.has(r)) continue;
            seen.add(r);
            roots.push({root: r, url: a.href || ""});
          }

          function pickLogo(teamBox) {
            if (!teamBox) return "";
            const img = teamBox.querySelector("img");
            if (!img) return "";
            return abs(
              img.getAttribute("data-src") ||
              img.getAttribute("data-lazy-src") ||
              img.getAttribute("data-original") ||
              img.getAttribute("src") || ""
            );
          }

          function pickName(teamBox) {
            if (!teamBox) return "";
            // نجمع نصوص العناصر ونختار أفضل نص (قصير، بدون أرقام/وقت)
            const texts = Array.from(teamBox.querySelectorAll("*"))
              .map(el => (el.textContent || "").trim())
              .filter(Boolean);

            // بعض الصفحات يكون الاسم آخر عنصر نصي
            const candidates = texts
              .map(t => t.replace(/\s+/g, " ").trim())
              .filter(t => t.length >= 2)
              .filter(t => !/\b\d{1,2}:\d{2}\b/.test(t))   // مو وقت
              .filter(t => !/^\d+$/.test(t));             // مو رقم

            if (candidates.length) {
              // اختار أقصر واحد (غالباً اسم الفريق يكون قصير)
              candidates.sort((a,b) => a.length - b.length);
              return candidates[0];
            }

            // fallback
            return (teamBox.textContent || "").replace(/\s+/g, " ").trim();
          }

          function pickTime(root) {
            // رجع أول HH:MM من عناصر قصيرة فقط
            const nodes = Array.from(root.querySelectorAll("span,div"));
            for (const n of nodes) {
              const t = (n.textContent || "").trim();
              if (!t) continue;
              const m = t.match(/\b\d{1,2}:\d{2}\b/);
              if (m && t.length <= 12) return m[0];
            }
            const m2 = (root.innerText || "").match(/\b\d{1,2}:\d{2}\b/);
            return m2 ? m2[0] : "";
          }

          function pickInfo(root) {
            const spans = Array.from(root.querySelectorAll("ul li span"))
              .map(s => (s.textContent || "").trim())
              .filter(Boolean);

            return {
              channel: spans[0] || "",
              commentator: spans[1] || "",
              competition: spans[2] || ""
            };
          }

          // نرجع status_raw حتى البايثون يحوله فقط لثلاث قيم
          function pickStatusRaw(root) {
            // نحاول نلتقط نص قريب يحتوي كلمات الحالة
            const re = /(لم تبدأ بعد|لم تبدأ|مباشر|انتهت|الشوط الأول|الشوط الثاني|استراحة|بين الشوطين|جارية|الوقت الإضافي|ركلات الترجيح)/;
            const nodes = Array.from(root.querySelectorAll("span,div"));
            let best = "";
            for (const n of nodes) {
              const t = (n.textContent || "").trim();
              if (!t) continue;
              const m = t.match(re);
              if (!m) continue;
              // خذ النص الأقصر (لتجنب نص الكرت الطويل)
              if (!best || t.length < best.length) best = t;
            }
            if (best) return best;

            const t2 = (root.innerText || "").trim();
            return t2;
          }

          const out = [];
          for (const item of roots) {
            const root = item.root;
            const tm1 = root.querySelector(".TM1");
            const tm2 = root.querySelector(".TM2");

            const info = pickInfo(root);

            out.push({
              home: pickName(tm1),
              away: pickName(tm2),
              home_logo: pickLogo(tm1),
              away_logo: pickLogo(tm2),
              time_local: pickTime(root),
              status_raw: pickStatusRaw(root),
              channel: info.channel,
              commentator: info.commentator,
              competition: info.competition,
              match_url: item.url || ""
            });
          }

          return out;
        }
        """

        cards = page.evaluate(js)

        # إذا فشل وجاب صفر: خزّن artifacts للتشخيص
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

    out = {"date": today, "source_url": url, "matches": []}

    for c in cards:
        home = (c.get("home") or "").strip()
        away = (c.get("away") or "").strip()
        if not home or not away:
            continue

        mid = f"{home[:12]}-{away[:12]}-{today}".replace(" ", "")

        ar_status = status_to_ar(c.get("status_raw") or "")
        out["matches"].append(
            {
                "id": mid,
                "home": home,
                "away": away,
                "home_logo": clean_logo(c.get("home_logo", "")),
                "away_logo": clean_logo(c.get("away_logo", "")),
                # الموقع يظهر HH:MM، وبغداد/الرياض نفس التوقيت عادةً
                "time_baghdad": (c.get("time_local") or "").strip(),
                "status": status_code(ar_status),
                "status_text": ar_status,          # ✅ فقط: لم تبدأ بعد | مباشر | انتهت
                "result_text": "",                 # ✅ دائمًا فاضي مثل ما تريد
                "channel": (c.get("channel") or "").strip() or None,
                "commentator": (c.get("commentator") or "").strip() or None,
                "competition": (c.get("competition") or "").strip() or None,
                "match_url": (c.get("match_url") or "").strip() or None,
                "_source": "yalla1shoot",
            }
        )

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[write] {OUT_PATH} with {len(out['matches'])} matches.")


if __name__ == "__main__":
    scrape()س
