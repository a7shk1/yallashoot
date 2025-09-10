import os, json, datetime as dt, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://www.yalla1shoot.com/matches-today_1/"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "shots"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def today_dir():
    d = OUT_DIR / dt.date.today().isoformat()
    d.mkdir(exist_ok=True, parents=True)
    return d

SELECTORS = [
    # جرّب بالترتيب – أول واحد يلقاه ويطلع بنتيجة نستخدمه
    "div.item",                      # شائع بالمواقع العربية
    "div.match-card",
    "section .item",
    ".matches .item",
    "ul.matches li",
    "article.match, article.card",
    "div.card:has(.team, .teams, .home, .away)",
]

def gradual_scroll(page, step=800, pause=0.25):
    page_height = page.evaluate("() => document.body.scrollHeight")
    y = 0
    while y < page_height:
        page.evaluate(f"window.scrollTo(0, {y});")
        time.sleep(pause)
        y += step
        page_height = page.evaluate("() => document.body.scrollHeight")

def main():
    date_str = dt.date.today().isoformat()
    out_day = today_dir()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
            locale="ar-IQ",
        )
        page = ctx.new_page()
        page.set_default_timeout(60000)

        print("[open]", URL)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        # انتظر أي عنصر فيه كلمة "مباريات" أو أقسام الصفحة
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        # مرّر الصفحة تحسباً لlazy-load
        gradual_scroll(page)

        # خزّن HTML كامل للديبَغ
        html_path = out_day / f"{date_str}_page.html"
        html_path.write_text(page.content(), encoding="utf-8")
        print("[debug] saved HTML:", html_path)

        # خزّن صورة كاملة للصفحة كـ fallback
        full_img = out_day / f"{date_str}_fullpage.png"
        page.screenshot(path=str(full_img), full_page=True)
        print("[debug] saved full-page screenshot:", full_img)

        # جرّب السيليكتورات بالترتيب
        cards_count = 0
        used_selector = None
        for sel in SELECTORS:
            loc = page.locator(sel)
            try:
                n = loc.count()
            except Exception:
                n = 0
            if n > 0:
                used_selector = sel
                cards_count = n
                break

        info = {
            "url": URL,
            "date": date_str,
            "selector_used": used_selector,
            "cards_found": cards_count,
            "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        }
        (out_day / f"{date_str}_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[info]", info)

        if not used_selector:
            print("[warn] no cards found with our selectors; rely on full-page screenshot only.")
        else:
            print(f"[grab] using selector: {used_selector} → {cards_count} cards")
            # التقط كل بطاقة على حدة
            for i in range(cards_count):
                card = page.locator(used_selector).nth(i)
                # أحيانًا تكون خارج الشاشة → Scroll إليها
                try:
                    card.scroll_into_view_if_needed(timeout=5000)
                except Exception:
                    pass
                shot_path = out_day / f"{date_str}_match_{i+1:02d}.png"
                try:
                    card.screenshot(path=str(shot_path))
                    print("saved", shot_path)
                except Exception as e:
                    print("screenshot failed for card", i+1, "->", e)

        browser.close()

if __name__ == "__main__":
    main()
