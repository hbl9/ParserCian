import asyncio
import os
import re
import requests
from playwright.async_api import async_playwright

USER_AGENT = os.getenv("USER_AGENT")
SAVE_DIR = "all_photos"
SEEN_FILE = "seen_offers.txt"
os.makedirs(SAVE_DIR, exist_ok=True)

seen = set()
if os.path.exists(SEEN_FILE):
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        seen = set(line.strip() for line in f if line.strip())

def save_seen(url):
    seen.add(url)
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def download_image(url, filepath):
    try:
        r = requests.get(url, timeout=15, stream=True, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
            return True
    except Exception:
        pass
    return False

def extract_offer_id(url):
    m = re.search(r"/(\d{6,})/?", url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]

def is_valid_image(src):
    if not src or src.startswith("data:") or ".svg" in src.lower():
        return False
    bad_markers = ["get-company-logo", "banks/", "avatar", "header-frontend", "mortgage-assets", "frontend-offer-card", "dmp.otm-r", "api.cian.ru/utr", "static.cdn-cian.ru/frontend"]
    return not any(marker in src.lower() for marker in bad_markers)

async def safe_goto(page, url, retries=3):
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return True
        except Exception as e:
            if "ERR_NAME_NOT_RESOLVED" in str(e) or "ERR_NETWORK_CHANGED" in str(e):
                await asyncio.sleep(3 * (attempt + 1))
            else:
                return False
    return False

async def process_offer_page(page, offer_url):
    ok = await safe_goto(page, offer_url)
    if not ok: return

    await page.wait_for_timeout(1500)
    image_urls = set()
    selectors = ['[data-name="GalleryInnerComponent"] img', '[data-name="Scroller"] img', '[data-name="ThumbComponent"] img', '[data-name="FloorPlan"] img', '[data-name="OfferCardPageLayoutCenter"] img']
    
    for sel in selectors:
        imgs = page.locator(sel)
        for i in range(await imgs.count()):
            src = await imgs.nth(i).get_attribute("src")
            if is_valid_image(src):
                image_urls.add(src.split("?")[0])

    if not image_urls: return

    offer_id = extract_offer_id(offer_url)
    offer_dir = os.path.join(SAVE_DIR, offer_id)
    
    if os.path.exists(offer_dir):
        return
        
    os.makedirs(offer_dir, exist_ok=True)

    unique_plans = {}
    for url in image_urls:
        match = re.search(r"/(\d+)[-_](\d+)\.(jpg|jpeg|png)$", url)
        if match:
            img_id, size_num = match.group(1), match.group(2)
            if img_id not in unique_plans or size_num == "1":
                unique_plans[img_id] = url
        else:
            unique_plans[url] = url

    for idx, (img_id, url) in enumerate(unique_plans.items()):
        ext = ".png" if ".png" in url.lower() else ".jpg"
        filepath = os.path.join(offer_dir, f"{offer_id}_{idx:02d}{ext}")
        download_image(url, filepath)
        
    with open(os.path.join(offer_dir, ".download_complete"), "w") as f:
        f.write("done")

async def main():
    REGION_IDS = [1, 2, 474, 460]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        for region_id in REGION_IDS:
            base_url = f"https://www.cian.ru/cat.php?deal_type=sale&engine_version=2&offer_type=flat&region={region_id}"
            page_num = 1
            empty_streak = 0

            while True:
                url = f"{base_url}&p={page_num}"
                print(f"\n--- Регион {region_id}, стр. {page_num} ---")
                ok = await safe_goto(page, url)
                if not ok:
                    page_num += 1
                    if page_num > 60: break
                    continue

                await page.wait_for_timeout(2000)
                cards = page.locator('article[data-name="CardComponent"]')
                if await cards.count() == 0:
                    empty_streak += 1
                    if empty_streak >= 2: break
                    page_num += 1
                    continue
                else:
                    empty_streak = 0

                links = await page.locator('article[data-name="CardComponent"] a[data-name="TitleComponent"]').all()
                offers = []
                for link in links:
                    href = await link.get_attribute("href")
                    if href:
                        if href.startswith("/"): href = "https://www.cian.ru" + href
                        base_url_offer = href.split("?")[0]
                        if base_url_offer not in seen and base_url_offer not in offers:
                            offers.append(base_url_offer)

                print(f"  Новых объявлений: {len(offers)}")
                for i, offer_url in enumerate(offers, 1):
                    print(f"  [{i}/{len(offers)}] Скачиваем: {offer_url}")
                    try:
                        await process_offer_page(page, offer_url)
                    except Exception as e:
                        print(f"    [-] Ошибка: {e}")
                    save_seen(offer_url)
                    await asyncio.sleep(1.5)
                page_num += 1
                await asyncio.sleep(3)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())