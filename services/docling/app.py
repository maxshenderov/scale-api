"""
ФАЗА 1: Docling FastAPI сервис для извлечения текста из PDF/JPG/PNG/DOCX/TXT
Использует IBM Docling для парсинга документов

Endpoint:
  POST /api/parse - парсит файл и возвращает JSON с текстом, таблицами, изображениями
  GET /health - проверка здоровья сервиса
"""

import logging
import base64
import json
import os
import ssl
from io import BytesIO
from pathlib import Path
from typing import Optional, List, Dict, Any

# ─────────────────────────────────────────────────────────────────────────────
# ОТКЛЮЧЕНИЕ SSL ДЛЯ КОРПОРАТИВНЫХ СЕТЕЙ
# ─────────────────────────────────────────────────────────────────────────────
# Если в переменной окружения задан DISABLE_SSL_VERIFY=1, отключаем проверку
# ВАЖНО: НЕ очищаем SSL_CERT_FILE — Rust Xet клиент (HuggingFace) нуждается в системных сертификатах
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["PYTHONHTTPSVERIFY"] = "0"

# Отключаем проверку SSL для urllib3 (requests использует его)
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    # Патчим SSL-контекст по умолчанию
    _original_https_context = ssl.create_default_context
    def _create_no_verify_context(*args, **kwargs):
        ctx = _original_https_context(*args, **kwargs)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ssl.create_default_context = _create_no_verify_context
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from pydantic import BaseModel
import uvicorn

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    logging.warning("Docling не установлен, будет использоваться fallback режим")

try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logging.warning("pytesseract/PIL не установлены, OCR изображений недоступен")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logging.warning("httpx не установлен, Vision LLM парсинг недоступен")

# ─────────────────────────────────────────────────────────────────────────────
# НАСТРОЙКИ VISION LLM (RouterAI)
# ─────────────────────────────────────────────────────────────────────────────
# Для JPG/PNG используем Vision LLM (Gemini) — гораздо лучше распознаёт
# сложные этикетки с цветными фонами, чем Tesseract
VISION_LLM_API_URL = os.environ.get("VISION_LLM_API_URL", "https://routerai.ru/api/v1/chat/completions")
VISION_LLM_API_KEY = os.environ.get("VISION_LLM_API_KEY", "sk-NlQDcBjddsD-4UIy6KLjKB8caO-DMX1r")
VISION_LLM_MODEL = os.environ.get("VISION_LLM_MODEL", "google/gemini-2.5-flash")

# ─────────────────────────────────────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# МОДЕЛИ ДАННЫХ
# ─────────────────────────────────────────────────────────────────────────────

class ImageData(BaseModel):
    """Модель для хранения изображения из документа"""
    index: int
    base64: str
    page: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class TableData(BaseModel):
    """Модель для хранения таблицы из документа"""
    index: int
    page: Optional[int] = None
    text: str
    markdown: Optional[str] = None
    type: str = "table"


class ParseResponse(BaseModel):
    """Ответ от парсера"""
    success: bool
    text: str
    confidence: float
    images: List[ImageData] = []
    tables: List[TableData] = []
    metadata: Dict[str, Any] = {}
    error: Optional[str] = None
    file_type: str
    pages: Optional[int] = None


class HealthResponse(BaseModel):
    """Ответ health check"""
    status: str
    docling_available: bool
    version: str = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI ПРИЛОЖЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Docling Phase 1 Parser",
    description="Сервис для парсинга PDF/JPG/PNG/DOCX/TXT и извлечения текста",
    version="1.0"
)

# Инициализируем Docling конвертер один раз при старте
converter = None

@app.on_event("startup")
async def startup_event():
    """Инициализация Docling при старте"""
    global converter
    if DOCLING_AVAILABLE:
        try:
            # Отключаем OCR — PDF от типографии содержит встроенный текст
            # OCR пытается скачать модели с modelscope.cn, что падает в корпоративных сетях
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = False
            pipeline_options.do_table_structure = True
            
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            logger.info("✅ Docling инициализирован успешно (OCR отключен)")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации Docling: {e}")
            converter = None
    else:
        logger.warning("⚠️ Docling недоступен, парсинг будет ограничен")


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────

def get_file_extension(filename: str) -> str:
    """Получить расширение файла"""
    return Path(filename).suffix.lower().lstrip(".")


def parse_text_file(data: bytes, encoding: str = "utf-8") -> str:
    """Парсинг TXT файла"""
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _preprocess_image_for_ocr(pil_image):
    """
    Предобработка изображения для улучшения качества OCR через OpenCV.
    Увеличивает контраст, бинаризует, убирает шум.
    """
    try:
        import cv2
        import numpy as np
        
        # Конвертируем PIL → OpenCV (numpy array)
        img_array = np.array(pil_image.convert("RGB"))
        img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        
        # 1. Увеличиваем изображение в 2 раза (для мелкого шрифта)
        h, w = img_bgr.shape[:2]
        img_bgr = cv2.resize(img_bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        
        # 2. Конвертируем в оттенки серого
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        
        # 3. CLAHE — адаптивное выравнивание гистограммы контраста
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        
        # 4. Удаление шума (median blur — хорошо сохраняет края текста)
        gray = cv2.medianBlur(gray, 3)
        
        # 5. Адаптивная бинаризация (хорошо работает с неравномерным освещением)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
        )
        
        # Конвертируем обратно в PIL
        result_image = Image.fromarray(binary)
        logger.info(f"🔧 OpenCV предобработка: {w}x{h} → {w*2}x{h*2}, CLAHE + adaptive threshold")
        return result_image
        
    except Exception as e:
        logger.warning(f"⚠️ OpenCV предобработка не удалась ({e}), используется оригинал")
        return pil_image


def parse_image_with_tesseract(data: bytes, file_type: str) -> Dict[str, Any]:
    """
    Парсинг изображений (JPG/PNG) через Tesseract OCR с предобработкой OpenCV.
    
    Пайплайн:
    1. Увеличение 2x (для мелкого шрифта)
    2. CLAHE контраст
    3. Адаптивная бинаризация
    4. Tesseract OCR (rus+eng)
    """
    if not TESSERACT_AVAILABLE:
        return {"success": False, "error": "pytesseract или PIL не установлены"}
    
    try:
        import io
        image = Image.open(io.BytesIO(data))
        
        # Конвертируем в RGB если нужно (для CMYK и других режимов)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        
        orig_w, orig_h = image.size
        logger.info(f"🔄 OCR изображения: {orig_w}x{orig_h} ({file_type})")
        
        # Предобработка через OpenCV
        processed_image = _preprocess_image_for_ocr(image)
        
        # Извлекаем текст с русским + английским языками
        # --psm 6: предполагаем единый блок текста (хорошо для этикеток)
        custom_config = r"--psm 6 --oem 3"
        text = pytesseract.image_to_string(processed_image, lang="rus+eng", config=custom_config)
        
        # Если результат пустой или очень короткий, пробуем с оригиналом (на всякий случай)
        if len(text.strip()) < 20:
            logger.info("⚠️ Мало текста с обработанного изображения, пробуем оригинал...")
            text_original = pytesseract.image_to_string(image, lang="rus+eng")
            if len(text_original.strip()) > len(text.strip()):
                text = text_original
        
        # Пытаемся получить confidence
        try:
            data_ocr = pytesseract.image_to_data(processed_image, lang="rus+eng", config=custom_config, output_type=pytesseract.Output.DICT)
            confidences = [int(c) for c in data_ocr["conf"] if int(c) > 0]
            avg_confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.5
        except Exception:
            avg_confidence = 0.5
        
        result = {
            "success": True,
            "text": text.strip(),
            "confidence": round(avg_confidence, 2),
            "images": [],
            "tables": [],
            "metadata": {
                "engine": "tesseract+opencv",
                "width": orig_w,
                "height": orig_h,
                "preprocessed": True,
            }
        }
        
        logger.info(f"✅ Tesseract: извлечено {len(text)} символов, confidence={avg_confidence:.0%}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка Tesseract парсинга: {e}")
        return {"success": False, "error": str(e)}


async def parse_image_with_vision_llm(data: bytes, file_type: str) -> Dict[str, Any]:
    """
    Парсинг изображений через Vision LLM (RouterAI/Gemini).
    Отправляет base64-изображение в OpenAI-compatible API.
    Fallback на Tesseract если LLM недоступен.
    """
    if not HTTPX_AVAILABLE:
        logger.warning("httpx не установлен, fallback на Tesseract")
        return parse_image_with_tesseract(data, file_type)
    
    try:
        import io
        image = Image.open(io.BytesIO(data)) if TESSERACT_AVAILABLE else None
        orig_w = image.size[0] if image else 0
        orig_h = image.size[1] if image else 0
    except Exception:
        orig_w = 0
        orig_h = 0
    
    # Определяем MIME-тип
    MIME_MAP = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
    mime = MIME_MAP.get(file_type, "image/jpeg")
    
    # Base64
    image_b64 = base64.b64encode(data).decode("utf-8")
    data_url = f"data:{mime};base64,{image_b64}"
    
    # Промпт — извлечение всего текста с этикетки
    prompt = """Проанализируй изображение этикетки/макета и извлеки ВЕСЬ видимый текст.
Верни результат как plain text (без markdown, без JSON, без комментариев).
Сохрани структуру: переносы строк между блоками, разделители между секциями.
Если видишь таблицы — сохрани их структуру.
Если видишь цвета (PANTONE, CMYK, RGB) — перечисли их.
Если видишь штрихкод — укажи его значение.
Если видишь的技术信息 (технология печати, размеры, тираж) — включи её."""
    
    # Формируем OpenAI-compatible запрос
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}}
            ]
        }
    ]
    
    payload = {
        "model": VISION_LLM_MODEL,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.1
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {VISION_LLM_API_KEY}"
    }
    
    try:
        logger.info(f"🔄 Vision LLM: отправка {file_type} ({len(data)} байт) → {VISION_LLM_MODEL}...")
        
        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            response = await client.post(VISION_LLM_API_URL, json=payload, headers=headers)
            response.raise_for_status()
        
        result_json = response.json()
        text = result_json["choices"][0]["message"]["content"]
        
        # Извлекаем стоимость
        cost = 0
        if "usage" in result_json and "cost" in result_json["usage"]:
            cost = result_json["usage"]["cost"]
        
        result = {
            "success": True,
            "text": text.strip(),
            "confidence": 0.95,
            "images": [],
            "tables": [],
            "metadata": {
                "engine": "vision_llm",
                "model": VISION_LLM_MODEL,
                "width": orig_w,
                "height": orig_h,
                "cost": cost,
            }
        }
        
        logger.info(f"✅ Vision LLM: извлечено {len(text)} символов, cost=${cost:.4f}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Vision LLM ошибка ({e}), fallback на Tesseract...")
        return parse_image_with_tesseract(data, file_type)


async def parse_with_docling(data: bytes, file_type: str, enable_ocr: bool = False) -> Dict[str, Any]:
    """Парсинг с использованием Docling"""
    
    if not DOCLING_AVAILABLE:
        return {"success": False, "error": "Docling недоступен"}
    
    try:
        # Сохраняем данные во временный файл
        temp_path = f"/tmp/docling_temp.{file_type}"
        with open(temp_path, "wb") as f:
            f.write(data)
        
        # Создаём конвертер с нужными настройками OCR
        if enable_ocr:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            pipeline_options.do_table_structure = True
            local_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        else:
            local_converter = converter
        
        logger.info(f"🔄 Парсинг {file_type} файла через Docling (OCR={'вкл' if enable_ocr else 'выкл'})...")
        doc = local_converter.convert(temp_path)
        
        # Извлекаем текст
        text = doc.document.export_to_markdown()
        
        # Инициализируем результат
        result = {
            "success": True,
            "text": text,
            "confidence": 0.85,
            "images": [],
            "tables": [],
            "metadata": {
                "pages": len(doc.pages) if hasattr(doc, "pages") else None,
            }
        }
        
        logger.info(f"✅ Docling: извлечено {len(text)} символов")
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка Docling парсинга: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья сервиса"""
    return HealthResponse(
        status="healthy",
        docling_available=DOCLING_AVAILABLE and converter is not None
    )


@app.post("/api/parse", response_model=ParseResponse)
async def parse_document(file: UploadFile = File(...)):
    """
    Парсит загруженный файл и извлекает текст, таблицы, изображения
    
    Поддерживаемые форматы:
    - PDF (через Docling)
    - JPG/PNG (через Docling + OCR)
    - DOCX (через Docling)
    - TXT (простое чтение)
    """
    
    if not file:
        raise HTTPException(status_code=400, detail="Файл не загружен")
    
    try:
        # Читаем файл
        file_data = await file.read()
        if not file_data:
            raise HTTPException(status_code=400, detail="Файл пуст")
        
        file_ext = get_file_extension(file.filename)
        logger.info(f"📁 Получен файл: {file.filename} ({file_ext})")
        
        # Поддерживаемые форматы
        supported_formats = ["pdf", "jpg", "jpeg", "png", "docx", "txt"]
        if file_ext not in supported_formats:
            raise HTTPException(
                status_code=400,
                detail=f"Формат {file_ext} не поддерживается. Поддерживаемые: {', '.join(supported_formats)}"
            )
        
        # Парсинг TXT локально (быстро)
        if file_ext == "txt":
            logger.info("📝 Парсинг TXT файла...")
            text = parse_text_file(file_data)
            return ParseResponse(
                success=True,
                text=text,
                confidence=1.0,
                file_type="txt",
                pages=1,
                metadata={"encoding": "utf-8"}
            )
        
        # Маршрутизация по типу файла
        IMAGE_FORMATS = ["jpg", "jpeg", "png"]
        PDF_FORMATS = ["pdf"]
        
        if file_ext in IMAGE_FORMATS:
            # JPG/PNG — через Tesseract OCR с предобработкой OpenCV
            logger.info(f"🖼️ Изображение {file_ext} → Tesseract OCR")
            result = parse_image_with_tesseract(file_data, file_ext)
        elif file_ext in PDF_FORMATS:
            # PDF — через Docling
            logger.info(f"📄 PDF → Docling")
            result = await parse_with_docling(file_data, file_ext)
        else:
            # DOCX и прочие — через Docling
            result = await parse_with_docling(file_data, file_ext)
        
        if not result.get("success", False):
            logger.error(f"❌ Парсинг не удался: {result.get('error')}")
            raise HTTPException(
                status_code=500,
                detail=f"Ошибка парсинга: {result.get('error', 'Неизвестная ошибка')}"
            )
        
        # Формируем успешный ответ
        return ParseResponse(
            success=True,
            text=result.get("text", ""),
            confidence=result.get("confidence", 0.85),
            images=result.get("images", []),
            tables=result.get("tables", []),
            file_type=file_ext,
            pages=result.get("metadata", {}).get("pages"),
            metadata=result.get("metadata", {})
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Критическая ошибка парсинга: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка сервера: {str(e)}"
        )


@app.post("/api/parse-base64", response_model=ParseResponse)
async def parse_document_base64(
    file_base64: str = Body(...),
    file_type: str = Body(...),
    filename: str = Body("document"),
    enable_ocr: bool = Body(False)
):
    """
    Парсит файл переданный в виде base64 строки
    
    Параметры:
    - file_base64: base64-кодированные данные файла
    - file_type: тип файла (pdf, jpg, png, docx, txt)
    - filename: имя файла (опционально)
    """
    
    try:
        # Декодируем base64
        file_data = base64.b64decode(file_base64)
        
        if not file_data:
            raise HTTPException(status_code=400, detail="Декодированные данные пусты")
        
        logger.info(f"📁 Получен base64 файл: {filename} ({file_type}), размер: {len(file_data)} байт")
        
        # Поддерживаемые форматы
        supported_formats = ["pdf", "jpg", "jpeg", "png", "docx", "txt"]
        if file_type.lower() not in supported_formats:
            raise HTTPException(
                status_code=400,
                detail=f"Формат {file_type} не поддерживается. Поддерживаемые: {', '.join(supported_formats)}"
            )
        
        # Парсинг TXT локально
        if file_type.lower() == "txt":
            logger.info("📝 Парсинг TXT файла (base64)...")
            text = parse_text_file(file_data)
            return ParseResponse(
                success=True,
                text=text,
                confidence=1.0,
                file_type="txt",
                pages=1,
                metadata={"encoding": "utf-8"}
            )
        
        # Маршрутизация по типу файла
        ft = file_type.lower()
        IMAGE_FORMATS = ["jpg", "jpeg", "png"]
        PDF_FORMATS = ["pdf"]
        
        if ft in IMAGE_FORMATS:
            # JPG/PNG — через Tesseract OCR с предобработкой OpenCV
            logger.info(f"🖼️ Изображение {ft} → Tesseract OCR")
            result = parse_image_with_tesseract(file_data, ft)
        elif ft in PDF_FORMATS:
            # PDF — через Docling
            logger.info(f"📄 PDF → Docling")
            result = await parse_with_docling(file_data, ft, enable_ocr=enable_ocr)
        else:
            # DOCX и прочие — через Docling
            result = await parse_with_docling(file_data, ft, enable_ocr=enable_ocr)
        
        if not result.get("success", False):
            logger.error(f"❌ Парсинг не удался: {result.get('error')}")
            raise HTTPException(
                status_code=500,
                detail=f"Ошибка парсинга: {result.get('error', 'Неизвестная ошибка')}"
            )
        
        return ParseResponse(
            success=True,
            text=result.get("text", ""),
            confidence=result.get("confidence", 0.85),
            images=result.get("images", []),
            tables=result.get("tables", []),
            file_type=ft,
            pages=result.get("metadata", {}).get("pages"),
            metadata=result.get("metadata", {})
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Критическая ошибка парсинга base64: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка сервера: {str(e)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("🚀 Запуск Docling FastAPI сервиса на http://localhost:8001")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info"
    )
