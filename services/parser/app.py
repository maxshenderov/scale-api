"""
Parser Service: PyMuPDF (PDF) + EasyOCR (JPG/PNG)
Бесплатный локальный парсер документов для типографии Лико.

Эндпоинты:
  POST /api/parse-base64  — парсит файл (base64) и возвращает текст
  GET  /health             — проверка здоровья сервиса
"""

import logging
import base64
import os
import ssl
import io
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

# ─────────────────────────────────────────────────────────────────────────────
# ОТКЛЮЧЕНИЕ SSL ДЛЯ КОРПОРАТИВНЫХ СЕТЕЙ
# ─────────────────────────────────────────────────────────────────────────────
os.environ["PYTHONHTTPSVERIFY"] = "0"

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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

# ─────────────────────────────────────────────────────────────────────────────
# ИМПОРТЫ ДВИЖКОВ
# ─────────────────────────────────────────────────────────────────────────────

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logging.warning("PyMuPDF не установлен — парсинг PDF недоступен")

try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False
    logging.warning("EasyOCR не установлен — парсинг изображений недоступен")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

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

class ParseResponse(BaseModel):
    success: bool
    text: str
    confidence: float = 0.0
    metadata: Dict[str, Any] = {}
    error: Optional[str] = None
    file_type: str

class HealthResponse(BaseModel):
    status: str
    pymupdf_available: bool
    easyocr_available: bool
    version: str = "2.0"

# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI ПРИЛОЖЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Parser Service",
    description="PyMuPDF + EasyOCR парсер документов",
    version="2.0"
)

# EasyOCR reader (инициализируется один раз при старте)
easyocr_reader = None

@app.on_event("startup")
async def startup_event():
    """Инициализация EasyOCR при старте (загрузка моделей)"""
    global easyocr_reader
    if EASYOCR_AVAILABLE:
        try:
            logger.info("🔄 Загрузка EasyOCR моделей (rus+eng)...")
            easyocr_reader = easyocr.Reader(
                ["ru", "en"],
                gpu=False,  # CPU режим
                verbose=False
            )
            logger.info("✅ EasyOCR инициализирован (rus+eng, CPU)")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации EasyOCR: {e}")
            easyocr_reader = None
    else:
        logger.warning("⚠️ EasyOCR недоступен")

# ─────────────────────────────────────────────────────────────────────────────
# ПАРСИНГ PDF — PyMuPDF
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text_structured(page) -> str:
    """
    Извлечение текста из страницы PDF через get_text("dict") с сортировкой
    блоков сверху-вниз, слева-направо. Даёт лучший порядок текста,
    чем простой get_text("text").
    """
    text_dict = page.get_text("dict", sort=True)
    lines = []
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:  # текстовый блок
            for line in block.get("lines", []):
                line_text = ""
                for span in line.get("spans", []):
                    line_text += span.get("text", "")
                if line_text.strip():
                    lines.append(line_text.strip())
    return "\n".join(lines)


def _extract_pdf_metadata(doc) -> Dict[str, Any]:
    """Извлечение метаданных PDF: title, author, filename и т.д."""
    meta = {}
    try:
        pdf_meta = doc.metadata or {}
        if pdf_meta.get("title"):
            meta["title"] = pdf_meta["title"]
        if pdf_meta.get("author"):
            meta["author"] = pdf_meta["author"]
        if pdf_meta.get("subject"):
            meta["subject"] = pdf_meta["subject"]
        if pdf_meta.get("keywords"):
            meta["keywords"] = pdf_meta["keywords"]
        if pdf_meta.get("creator"):
            meta["creator"] = pdf_meta["creator"]
        if pdf_meta.get("producer"):
            meta["producer"] = pdf_meta["producer"]
        if pdf_meta.get("creationDate"):
            meta["creation_date"] = pdf_meta["creationDate"]
        if pdf_meta.get("modDate"):
            meta["mod_date"] = pdf_meta["modDate"]
    except Exception:
        pass
    return meta


def _extract_key_patterns(text: str) -> Dict[str, Any]:
    """
    Извлечение ключевых паттернов из текста:
    - PANTONE цвета
    - Размеры (mm, мм)
    - Название файла (.ai, .pdf)
    - Технология печати (FX, OS, SS)
    """
    import re
    patterns = {}

    # PANTONE цвета
    pantone_matches = re.findall(r'PANTONE\s+[\w\s]+(?:C|U|M)\b', text, re.IGNORECASE)
    if pantone_matches:
        patterns["pantone_colors"] = list(set(p.strip() for p in pantone_matches))

    # Размеры в мм
    dim_matches = re.findall(r'(\d+[.,]?\d*)\s*mm\b', text, re.IGNORECASE)
    if dim_matches:
        patterns["dimensions_mm"] = list(set(dim_matches))

    # Файлы (.ai, .pdf, .psd и т.д.)
    file_matches = re.findall(r'[\w\-_.]+\.(?:ai|pdf|psd|indd|cdr)', text, re.IGNORECASE)
    if file_matches:
        patterns["source_files"] = list(set(file_matches))

    # Технология печати
    tech_matches = re.findall(r'(?:FX|OS|SS)\s*[=:]\s*\w+', text)
    if tech_matches:
        patterns["print_tech"] = list(set(tech_matches))

    # Номер заказа / код этикетки (по паттерну数字_буквы)
    order_matches = re.findall(r'\b\d{5,}_[\w_]+\b', text)
    if order_matches:
        patterns["order_codes"] = list(set(order_matches))

    return patterns


def parse_pdf_with_pymupdf(data: bytes) -> Dict[str, Any]:
    """
    Извлечение текста из PDF через PyMuPDF (fitz).
    Использует get_text("dict") для структурированного извлечения
    с сортировкой блоков по позиции, извлечением метаданных
    и парсингом ключевых паттернов.
    """
    if not PYMUPDF_AVAILABLE:
        return {"success": False, "error": "PyMuPDF не установлен"}
    
    try:
        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        
        doc = fitz.open(tmp_path)
        
        pages_count = len(doc)
        
        # Извлекаем метаданные PDF
        pdf_meta = _extract_pdf_metadata(doc)
        
        all_text = []
        total_chars = 0
        
        for page_num in range(pages_count):
            page = doc[page_num]
            
            # Структурированное извлечение (dict + sort)
            text = _extract_text_structured(page)
            
            if text.strip():
                all_text.append(f"--- Страница {page_num + 1} ---\n{text.strip()}")
                total_chars += len(text.strip())
        
        doc.close()
        
        # Удаляем временный файл
        os.unlink(tmp_path)
        
        full_text = "\n\n".join(all_text)
        
        if not full_text.strip():
            return {
                "success": True,
                "text": "",
                "confidence": 0.0,
                "metadata": {
                    "engine": "pymupdf",
                    "pages": pages_count,
                    "has_text": False,
                    "note": "PDF не содержит встроенного текста (сканированный PDF)",
                    **pdf_meta
                }
            }
        
        # Извлекаем ключевые паттерны
        key_patterns = _extract_key_patterns(full_text)
        
        # Формируем структурированный заголовок
        header_parts = []
        if pdf_meta.get("title"):
            header_parts.append(f"Title: {pdf_meta['title']}")
        if pdf_meta.get("author"):
            header_parts.append(f"Author: {pdf_meta['author']}")
        if key_patterns.get("source_files"):
            header_parts.append(f"Source: {', '.join(key_patterns['source_files'])}")
        if key_patterns.get("dimensions_mm"):
            header_parts.append(f"Dimensions: {', '.join(key_patterns['dimensions_mm'])} mm")
        if key_patterns.get("pantone_colors"):
            header_parts.append(f"PANTONE: {', '.join(key_patterns['pantone_colors'])}")
        if key_patterns.get("print_tech"):
            header_parts.append(f"Tech: {', '.join(key_patterns['print_tech'])}")
        if key_patterns.get("order_codes"):
            header_parts.append(f"Order: {', '.join(key_patterns['order_codes'])}")
        
        # Собираем финальный текст с заголовком
        if header_parts:
            header = "=== PDF INFO ===\n" + "\n".join(header_parts) + "\n=== END INFO ===\n\n"
            full_text = header + full_text
        
        # Конфиденциальность: выше если есть метаданные и паттерны
        has_metadata = bool(pdf_meta)
        has_patterns = bool(key_patterns)
        confidence = 0.95
        if has_metadata and has_patterns:
            confidence = 0.98
        elif has_metadata or has_patterns:
            confidence = 0.96
        
        result = {
            "success": True,
            "text": full_text,
            "confidence": confidence,
            "metadata": {
                "engine": "pymupdf",
                "pages": pages_count,
                "has_text": True,
                "chars": total_chars,
                **pdf_meta,
                **key_patterns
            }
        }
        
        logger.info(f"✅ PyMuPDF: {total_chars} символов из {pages_count} стр.")
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка PyMuPDF: {e}")
        return {"success": False, "error": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# ПАРСИНГ ИЗОБРАЖЕНИЙ — EasyOCR
# ─────────────────────────────────────────────────────────────────────────────

def parse_image_with_easyocr(data: bytes, file_type: str) -> Dict[str, Any]:
    """
    Извлечение текста из JPG/PNG через EasyOCR.
    Локально, бесплатно, поддержка русского языка.
    """
    if not EASYOCR_AVAILABLE or easyocr_reader is None:
        return {"success": False, "error": "EasyOCR не инициализирован"}
    
    try:
        # Открываем изображение
        image = Image.open(io.BytesIO(data))
        
        # Конвертируем в RGB если нужно
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        
        # Конвертируем в numpy array для EasyOCR
        import numpy as np
        img_array = np.array(image)
        
        orig_w, orig_h = image.size
        logger.info(f"🔄 EasyOCR: парсинг изображения {orig_w}x{orig_h} ({file_type})...")
        
        # Распознаём текст
        results = easyocr_reader.readtext(img_array)
        
        # results = list of (bbox, text, confidence)
        texts = []
        total_confidence = 0
        
        for (bbox, text, conf) in results:
            if text.strip():
                texts.append(text.strip())
                total_confidence += conf
        
        avg_confidence = total_confidence / len(results) if results else 0.0
        full_text = "\n".join(texts)
        
        result = {
            "success": True,
            "text": full_text,
            "confidence": round(avg_confidence, 2),
            "metadata": {
                "engine": "easyocr",
                "width": orig_w,
                "height": orig_h,
                "regions": len(results),
                "chars": len(full_text)
            }
        }
        
        logger.info(f"✅ EasyOCR: {len(full_text)} символов из {len(results)} областей")
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка EasyOCR: {e}")
        return {"success": False, "error": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# СТРУКТУРИРОВАННОЕ ИЗВЛЕЧЕНИЕ JSON
# ─────────────────────────────────────────────────────────────────────────────

def _text_to_structured_json(raw_text: str, filename: str = "", file_type: str = "", metadata: dict = None) -> Dict[str, Any]:
    """
    Преобразует сырой текст парсера в структурированный JSON.
    Извлекает: название этикетки, размеры, PANTONE цвета, технологию печати,
    номер заказа, подложку, количество страниц и т.д.
    """
    import re

    structured = {
        "label": {
            "name": "",
            "order_code": "",
            "source_file": "",
            "dimensions_mm": {},
            "substrate": "",
            "page_count": 0
        },
        "colors": [],
        "print_technology": [],
        "raw_text_preview": raw_text[:500] if raw_text else ""
    }

    if not raw_text:
        return structured

    # ── Название этикетки / код заказа ──
    # Паттерн: 3050609_Ceresit_CS25_07_Gray_280ml
    order_match = re.search(r'\b(\d{5,}_[\w_]+)\b', raw_text)
    if order_match:
        structured["label"]["order_code"] = order_match.group(1)
        # Читаемое название: заменяем _ на пробелы
        structured["label"]["name"] = order_match.group(1).replace("_", " ")

    # Файл-источник (.ai, .pdf, .psd)
    source_match = re.search(r'([\w\-_.]+\.(?:ai|pdf|psd|indd|cdr))', raw_text, re.IGNORECASE)
    if source_match:
        structured["label"]["source_file"] = source_match.group(1)
    elif filename:
        structured["label"]["source_file"] = filename

    # ── Размеры ──
    dim_matches = re.findall(r'(\d+[.,]?\d*)\s*mm\b', raw_text, re.IGNORECASE)
    if dim_matches:
        unique_dims = sorted(set(d.replace(",", ".") for d in dim_matches), key=lambda x: float(x))
        if len(unique_dims) >= 2:
            structured["label"]["dimensions_mm"] = {
                "width_mm": float(unique_dims[0]),
                "height_mm": float(unique_dims[-1])
            }
        elif len(unique_dims) == 1:
            structured["label"]["dimensions_mm"] = {"value_mm": float(unique_dims[0])}

    # ── Подложка (Substrate) ──
    substrate_match = re.search(r'Substrate\s*[:\s]*(\w[\w\s]*?)(?:\n|$)', raw_text, re.IGNORECASE)
    if substrate_match:
        structured["label"]["substrate"] = substrate_match.group(1).strip()

    # ── Количество страниц ──
    pages_match = re.search(r'Number of pages\s*[:\s]*(\d+/\d+|\d+)', raw_text, re.IGNORECASE)
    if pages_match:
        structured["label"]["page_count"] = pages_match.group(1)

    # ── PANTONE цвета ──
    pantone_matches = re.findall(r'(PANTONE\s+[\w\s]+?(?:\s+C\b|\s+U\b|\s+M\b))', raw_text, re.IGNORECASE)
    # Также "Black 1", "Black 2", "Gloss Varnish"
    other_colors = re.findall(r'\b(Black\s*\d*|Gloss\s+Varnish|Yellow|Magenta|Cyan)\b', raw_text, re.IGNORECASE)
    
    all_colors = list(set(c.strip() for c in pantone_matches))
    for c in other_colors:
        c_clean = c.strip()
        if c_clean not in all_colors:
            all_colors.append(c_clean)
    structured["colors"] = all_colors

    # ── Технология печати ──
    tech_map = {
        "FX": "Flexo",
        "OS": "Offset",
        "SS": "Silkscreen"
    }
    tech_matches = re.findall(r'(FX|OS|SS)\s*[=:]\s*(\w+)', raw_text)
    for code, name in tech_matches:
        tech_entry = {"code": code, "name": name.strip()}
        if tech_entry not in structured["print_technology"]:
            structured["print_technology"].append(tech_entry)

    # Если нашли "FX = Flexo" и т.д. в тексте, но не через паттерн
    if not structured["print_technology"]:
        if re.search(r'\bFlexo\b', raw_text, re.IGNORECASE):
            structured["print_technology"].append({"code": "FX", "name": "Flexo"})
        if re.search(r'\bOffset\b', raw_text, re.IGNORECASE):
            structured["print_technology"].append({"code": "OS", "name": "Offset"})
        if re.search(r'\bSilkscreen\b', raw_text, re.IGNORECASE):
            structured["print_technology"].append({"code": "SS", "name": "Silkscreen"})

    # ── Метаданные парсера ──
    structured["parser"] = {
        "engine": (metadata or {}).get("engine", "unknown"),
        "file_type": file_type,
        "total_chars": len(raw_text)
    }

    return structured


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────

def get_file_extension(filename: str) -> str:
    """Получить расширение файла"""
    return Path(filename).suffix.lower().lstrip(".")

# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья сервиса"""
    return HealthResponse(
        status="healthy",
        pymupdf_available=PYMUPDF_AVAILABLE,
        easyocr_available=EASYOCR_AVAILABLE and easyocr_reader is not None
    )


@app.post("/api/parse-base64", response_model=ParseResponse)
async def parse_document_base64(
    file_base64: str = Body(...),
    file_type: str = Body(...),
    filename: str = Body("document"),
    enable_ocr: bool = Body(False),
    format: str = Body("text")
):
    """
    Парсит файл переданный в виде base64 строки.
    
    Параметры:
    - format="text"  — возвращает сырой текст (по умолчанию)
    - format="json"  — возвращает структурированный JSON с извлечёнными параметрами этикетки
    
    Маршрутизация:
    - PDF → PyMuPDF (бесплатно, локально)
    - JPG/PNG → EasyOCR (бесплатно, локально, rus+eng)
    - TXT → прямое чтение
    """
    
    try:
        # Декодируем base64
        file_data = base64.b64decode(file_base64)
        
        if not file_data:
            raise HTTPException(status_code=400, detail="Декодированные данные пусты")
        
        ft = file_type.lower()
        logger.info(f"📁 Файл: {filename} ({ft}), {len(file_data)} байт, format={format}")
        
        # Маршрутизация по типу файла
        if ft == "txt":
            text = file_data.decode("utf-8", errors="replace")
            parsed_result = {
                "success": True,
                "text": text,
                "confidence": 1.0,
                "metadata": {"engine": "raw", "chars": len(text)}
            }
        
        elif ft in ("pdf",):
            parsed_result = parse_pdf_with_pymupdf(file_data)
        
        elif ft in ("jpg", "jpeg", "png"):
            parsed_result = parse_image_with_easyocr(file_data, ft)
        
        else:
            # Попытка: PDF → text, иначе ошибка
            parsed_result = parse_pdf_with_pymupdf(file_data)
        
        if not parsed_result.get("success", False):
            raise HTTPException(
                status_code=500,
                detail=f"Ошибка парсинга: {parsed_result.get('error', 'Неизвестная ошибка')}"
            )
        
        # Если запрошен структурированный JSON — преобразуем
        if format.lower() == "json":
            raw_text = parsed_result.get("text", "")
            structured = _text_to_structured_json(
                raw_text,
                filename=filename,
                file_type=ft,
                metadata=parsed_result.get("metadata", {})
            )
            # Возвращаем JSON как текст в поле text (формат ParseResponse)
            import json
            json_text = json.dumps(structured, ensure_ascii=False, indent=2)
            return ParseResponse(
                success=True,
                text=json_text,
                confidence=parsed_result.get("confidence", 0.5),
                file_type=ft,
                metadata={"format": "json", **(parsed_result.get("metadata", {}))}
            )
        
        return ParseResponse(
            success=True,
            text=parsed_result.get("text", ""),
            confidence=parsed_result.get("confidence", 0.5),
            file_type=ft,
            metadata=parsed_result.get("metadata", {})
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка сервера: {str(e)}"
        )


@app.post("/api/parse", response_model=ParseResponse)
async def parse_document(file: UploadFile = File(...)):
    """Парсит загруженный файл (multipart/form-data)"""
    
    if not file:
        raise HTTPException(status_code=400, detail="Файл не загружен")
    
    try:
        file_data = await file.read()
        if not file_data:
            raise HTTPException(status_code=400, detail="Файл пуст")
        
        file_ext = get_file_extension(file.filename)
        logger.info(f"📁 Файл: {file.filename} ({file_ext}), {len(file_data)} байт")
        
        if file_ext == "txt":
            text = file_data.decode("utf-8", errors="replace")
            return ParseResponse(
                success=True,
                text=text,
                confidence=1.0,
                file_type="txt",
                metadata={"engine": "raw", "chars": len(text)}
            )
        elif file_ext in ("pdf",):
            result = parse_pdf_with_pymupdf(file_data)
        elif file_ext in ("jpg", "jpeg", "png"):
            result = parse_image_with_easyocr(file_data, file_ext)
        else:
            result = parse_pdf_with_pymupdf(file_data)
        
        if not result.get("success", False):
            raise HTTPException(
                status_code=500,
                detail=f"Ошибка парсинга: {result.get('error', 'Неизвестная ошибка')}"
            )
        
        return ParseResponse(
            success=True,
            text=result.get("text", ""),
            confidence=result.get("confidence", 0.5),
            file_type=file_ext,
            metadata=result.get("metadata", {})
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка сервера: {str(e)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("🚀 Запуск Parser Service на http://localhost:8002")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        log_level="info"
    )
