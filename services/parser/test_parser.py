"""Тест Parser Service: PyMuPDF + EasyOCR"""
import base64
import json
import sys
import httpx

URL = "http://localhost:8002/api/parse-base64"

def test_file(filepath, file_type):
    print(f"\n{'='*60}")
    print(f"Тест: {filepath} ({file_type})")
    print(f"{'='*60}")
    
    with open(filepath, "rb") as f:
        data = f.read()
    
    b64 = base64.b64encode(data).decode()
    print(f"Размер: {len(data)} байт, base64: {len(b64)} символов")
    
    payload = {
        "file_base64": b64,
        "file_type": file_type,
        "filename": filepath.split("/")[-1].split("\\")[-1]
    }
    
    print(f"Отправка на {URL}...")
    resp = httpx.post(URL, json=payload, timeout=120)
    
    result = resp.json()
    print(f"\nСтатус: {resp.status_code}")
    print(f"Успех: {result.get('success')}")
    print(f"Движок: {result.get('metadata', {}).get('engine', '?')}")
    print(f"Confidence: {result.get('confidence', 0):.2f}")
    
    text = result.get("text", "")
    print(f"Символов: {len(text)}")
    print(f"\n--- Текст (первые 500 символов) ---")
    print(text[:500])
    print(f"--- Конец ---\n")
    
    return result

if __name__ == "__main__":
    # Тест PDF
    test_file("../../PDF/8516304_3050609_Ceresit_CS25_07_Gray_280ml_72,5x180mm.pdf", "pdf")
    
    # Тест JPG
    test_file("../../jpg/Снимок экрана 2026-05-08 162528.jpg", "jpg")
