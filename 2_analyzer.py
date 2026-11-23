import os
import time
import shutil
import cv2
import numpy as np
import re
from PIL import Image
import pytesseract
from roboflow import Roboflow

SAVE_DIR = "all_photos"
CHECKED_DIR = "CHECKED"
NOT_FOUND_DIR = "NOT_FOUND"

ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY")
ROBOFLOW_WORKSPACE = os.getenv("ROBOFLOW_WORKSPACE")
ROBOFLOW_PROJECT = os.getenv("ROBOFLOW_PROJECT")

MIN_PLAN_RATING = 10.0

# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

for directory in [SAVE_DIR, CHECKED_DIR, NOT_FOUND_DIR]:
    os.makedirs(directory, exist_ok=True)

missing_settings = [
    name for name, value in [
        ("ROBOFLOW_API_KEY", ROBOFLOW_API_KEY),
        ("ROBOFLOW_WORKSPACE", ROBOFLOW_WORKSPACE),
        ("ROBOFLOW_PROJECT", ROBOFLOW_PROJECT),
    ]
    if not value
]
if missing_settings:
    raise RuntimeError(
        "Не заданы переменные в .env: " + ", ".join(missing_settings)
    )

rf = Roboflow(api_key=ROBOFLOW_API_KEY)
project = rf.workspace(ROBOFLOW_WORKSPACE).project(ROBOFLOW_PROJECT)

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
            img_pil = img_pil.resize(
                (1500, int(1500 * img_pil.height / img_pil.width))
            )

        img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)

        mask_vibrant = cv2.inRange(
            hsv, np.array([0, 50, 50]), np.array([180, 255, 255])
        )
        vibrant_ratio = np.sum(mask_vibrant == 255) / (
            img_cv.shape[0] * img_cv.shape[1]
        )
        if vibrant_ratio > 0.08:
            rating -= 20.0

        mask_light = cv2.inRange(
            hsv, np.array([0, 0, 200]), np.array([180, 60, 255])
        )
        light_ratio = np.sum(mask_light == 255) / (
            img_cv.shape[0] * img_cv.shape[1]
        )
        rating += light_ratio * 10.0

        width, height = img_pil.size
        step = 64
        graphic_tiles, total_tiles = 0, 0

        for y in range(0, height - step + 1, step * 2):
            for x in range(0, width - step + 1, step * 2):
                total_tiles += 1
                tile_np = np.array(
                    img_pil.crop((x, y, x + step, y + step)).convert("L")
                )
                total_pixels = tile_np.size
                white = np.sum(tile_np > 240)
                dark = np.sum(tile_np < 40)
                mid = total_pixels - white - dark

                if (
                    (white / total_pixels > 0.4 or dark / total_pixels > 0.1)
                    and (mid / total_pixels < 0.3)
                ):
                    graphic_tiles += 1

        if total_tiles > 0:
            rating += (graphic_tiles / total_tiles) * 10.0

        try:
            text = pytesseract.image_to_string(
                img_pil, lang="rus+eng", config="--psm 11"
            ).lower()
            matches = [word for word in PLAN_KEYWORDS if word in text]
            digits = re.findall(r"\d+[,.]\d+", text)

            if matches:
                rating += len(matches) * 3.0
                found_text_info += f"Слова: {matches[:3]}. "
            if digits:
                rating += len(digits) * 1.0
                found_text_info += f"Цифры: {len(digits)}. "
        except Exception as exc:
            print(f"[OCR] Ошибка распознавания {image_path}: {exc}")

    except Exception as exc:
        print(f"[Image] Не удалось обработать {image_path}: {exc}")
        return -100.0, "Ошибка"

    return rating, found_text_info


def upload_to_roboflow(image_path, offer_id, rating):
    try:
        project.upload(
            image_path=image_path,
            batch_name=f"cian_{offer_id}",
            tag_names=["cian", "floor_plan", "auto_selected"],
            num_retry_uploads=2,
        )
        print(
            f"[Roboflow] Загружено: {offer_id} "
            f"(рейтинг {rating:.1f})"
        )
        return True
    except Exception as exc:
        print(f"[Roboflow] Ошибка загрузки {offer_id}: {exc}")
        return False


def analyze_folder(offer_id):
    folder_path = os.path.join(SAVE_DIR, offer_id)

    if not os.path.isdir(folder_path):
        return True

    files = [
        filename
        for filename in os.listdir(folder_path)
        if filename.lower().endswith((".jpg", ".jpeg", ".png"))
    ]

    if not files:
        print(f"[{offer_id}] Нет изображений")
        shutil.rmtree(folder_path)
        return True

    best_file = None
    max_rating = -100.0
    best_text_info = ""

    for filename in files:
        image_path = os.path.join(folder_path, filename)
        rating, text_info = calculate_rating(image_path)

        if rating > max_rating:
            max_rating = rating
            best_file = filename
            best_text_info = text_info

    if not best_file or max_rating < MIN_PLAN_RATING:
        print(f"[{offer_id}] Планировка не найдена (макс. рейтинг: {max_rating:.1f})")
        destination = os.path.join(NOT_FOUND_DIR, offer_id)
        if os.path.exists(destination):
            shutil.rmtree(destination)
        shutil.move(folder_path, destination)
        return True

    best_image_path = os.path.join(folder_path, best_file)
    print(
        f"[{offer_id}] Лучший кандидат: {best_file} "
        f"(рейтинг: {max_rating:.1f}) {best_text_info}"
    )

    uploaded = upload_to_roboflow(
        image_path=best_image_path,
        offer_id=offer_id,
        rating=max_rating,
    )

    if not uploaded:
        print(f"[{offer_id}] Оставляем объявление для повторной загрузки")
        return False

    destination = os.path.join(CHECKED_DIR, offer_id)
    if os.path.exists(destination):
        shutil.rmtree(destination)
    shutil.move(folder_path, destination)
    print(f"[{offer_id}] Обработка завершена")
    return True


def main():
    print("Анализатор запущен. Отправка лучших кандидатов напрямую в Roboflow.")

    while True:
        for item in os.listdir(SAVE_DIR):
            folder_path = os.path.join(SAVE_DIR, item)
            if not os.path.isdir(folder_path):
                continue

            marker = os.path.join(folder_path, ".download_complete")
            if not os.path.exists(marker):
                continue

            try:
                completed = analyze_folder(item)
                if completed and os.path.exists(marker):
                    os.remove(marker)
            except Exception as exc:
                print(f"[{item}] Ошибка анализа: {exc}")

        time.sleep(3)


if __name__ == "__main__":
    main()
