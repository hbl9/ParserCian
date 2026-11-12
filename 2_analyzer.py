import os
import time
import shutil
import cv2
import numpy as np
import re
from PIL import Image
import pytesseract

# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

SAVE_DIR = "all_photos"
RESULT_PLANS_DIR = "RESULT_PLANS"
CHECKED_DIR = "CHECKED"
NOT_FOUND_DIR = "NOT_FOUND"

for d in [RESULT_PLANS_DIR, CHECKED_DIR, NOT_FOUND_DIR]:
    os.makedirs(d, exist_ok=True)

PLAN_KEYWORDS = [
    "спальня", "кухня", "гостиная", "санузел", "с/у", "ванная", "коридор", 
    "прихожая", "гардероб", "холл", "постирочная", "балкон", "лоджия",
    "площадь", "этаж", "комната", "жилая", "двор", "м2", "м²", "вход", "вид"
]

def calculate_rating(image_path):
    rating = 0.0
    found_text_info = ""
    try:
        img_pil = Image.open(image_path).convert("RGB")
        if img_pil.width > 1500:
            img_pil = img_pil.resize((1500, int(1500 * img_pil.height / img_pil.width)))
            
        img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)

        mask_vibrant = cv2.inRange(hsv, np.array([0, 50, 50]), np.array([180, 255, 255]))
        vibrant_ratio = np.sum(mask_vibrant == 255) / (img_cv.shape[0] * img_cv.shape[1])
        if vibrant_ratio > 0.08: rating -= 20.0

        mask_light = cv2.inRange(hsv, np.array([0, 0, 200]), np.array([180, 60, 255]))
        light_ratio = np.sum(mask_light == 255) / (img_cv.shape[0] * img_cv.shape[1])
        rating += light_ratio * 10.0

        width, height = img_pil.size
        step = 64
        graphic_tiles, total_tiles = 0, 0
        for y in range(0, height - step + 1, step * 2):
            for x in range(0, width - step + 1, step * 2):
                total_tiles += 1
                tile_np = np.array(img_pil.crop((x, y, x + step, y + step)).convert("L"))
                total_pixels = tile_np.size
                white, dark = np.sum(tile_np > 240), np.sum(tile_np < 40)
                mid = total_pixels - white - dark
                if (white/total_pixels > 0.4 or dark/total_pixels > 0.1) and (mid/total_pixels < 0.3):
                    graphic_tiles += 1
        
        if total_tiles > 0:
            rating += (graphic_tiles / total_tiles) * 10.0

        try:
            text = pytesseract.image_to_string(img_pil, lang='rus+eng', config='--psm 11').lower()
            matches = [word for word in PLAN_KEYWORDS if word in text]
            digits = re.findall(r'\d+[,.]\d+', text)
            if matches:
                rating += len(matches) * 3.0
                found_text_info += f"Слова: {matches[:3]}. "
            if digits:
                rating += len(digits) * 1.0
                found_text_info += f"Цифры: {len(digits)}. "
        except Exception: pass
    except Exception: return -100.0, "Ошибка"
    return rating, found_text_info

def analyze_folder(offer_id):
    folder_path = os.path.join(SAVE_DIR, offer_id)
    files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    if not files: 
        shutil.rmtree(folder_path)
        return

    best_file, max_rating, best_text_info = None, -100.0, ""
    for file in files:
        rating, text_info = calculate_rating(os.path.join(folder_path, file))
        if rating > max_rating:
            max_rating, best_file, best_text_info = rating, file, text_info

    if best_file and max_rating >= 10.0:
        print(f"[{offer_id}] Планировка: {best_file} (Рейтинг: {max_rating:.1f}) {best_text_info}")
        shutil.copy2(os.path.join(folder_path, best_file), os.path.join(RESULT_PLANS_DIR, f"{offer_id}_plan.jpg"))
        shutil.move(folder_path, os.path.join(CHECKED_DIR, offer_id))
    else:
        print(f"[{offer_id}] Не найдена (Макс: {max_rating:.1f})")
        shutil.move(folder_path, os.path.join(NOT_FOUND_DIR, offer_id))

def main():
    print("Анализатор запущен. Ждем новые папки в 'all_photos'...")
    while True:
        # Ищем папки, в которых есть маркер завершения скачивания
        for item in os.listdir(SAVE_DIR):
            folder_path = os.path.join(SAVE_DIR, item)
            if os.path.isdir(folder_path):
                marker = os.path.join(folder_path, ".download_complete")
                if os.path.exists(marker):
                    os.remove(marker) # Убираем маркер, чтобы не обрабатывать дважды
                    try:
                        analyze_folder(item)
                    except Exception as e:
                        print(f"Ошибка анализа {item}: {e}")
        time.sleep(3) # Проверяем каждые 3 секунды

if __name__ == "__main__":
    main()