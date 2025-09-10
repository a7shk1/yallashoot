import os, datetime as dt
from playwright.sync_api import sync_playwright

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "shots")
os.makedirs(OUT_DIR, exist_ok=True)

URL = "https://www.yalla1shoot.com/matches-today_1/"

def main():
    today = dt.date.today().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, timeout=60000)

        # نحدد كل بطاقة لعبة
        cards = page.locator("div.item")   # لازم تتأكد من الـ selector حسب HTML

        count = cards.count()
        print(f"Found {count} match cards")

        for i in range(count):
            card = cards.nth(i)
            path = os.path.join(OUT_DIR, f"{today}_match{i+1}.png")
            card.screenshot(path=path)
            print("Saved", path)

        browser.close()

if __name__ == "__main__":
    main()
