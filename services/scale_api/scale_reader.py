"""
Весовой парсер — читает данные с M2M WiFi-модуля весов СКУ I2121 (СКИ-12/Yaohua).

Настройки модуля (по умолчанию 192.168.12.147):
  - Data Transfer Mode: Transparent (0)
  - 485 mode: ON
  - Baudrate: 9600, 8N1
  - Network: Server, TCP, Port 8899

Формат данных Yaohua: WWMMMMMMUU\r\n
  W = статус (w=стабильно)
  M = тип веса (n=нетто, g=брутто)
  MMMMMM = значение с десятичной точкой (напр. 00005.0)
  UU = единицы (kg)
"""

import os
import socket
import re
from typing import NamedTuple


class WeightReading(NamedTuple):
    raw: str
    value: float
    unit: str
    stable: bool
    mode: str  # 'n' = net, 'g' = gross


def parse_weight(line: str) -> WeightReading | None:
    """Parse Yaohua weight format: WWMMMMMMUU"""
    line = line.strip()
    if not line or len(line) < 8:
        return None

    # Format: <status><mode><value><unit>
    # Example: wn00005.0kg
    match = re.match(r'^(\w)(\w)([\d.]+)(\w{2})$', line)
    if not match:
        return None

    status_char, mode_char, value_str, unit = match.groups()

    return WeightReading(
        raw=line,
        value=float(value_str),
        unit=unit,
        stable=(status_char == 'w'),
        mode='n' if mode_char == 'n' else mode_char,
    )


def _get_config():
    """Read scale connection settings from environment."""
    return (
        os.getenv("SCALE_HOST", "192.168.12.147"),
        int(os.getenv("SCALE_PORT", "8899")),
    )


def read_weight(host: str | None = None, port: int | None = None, timeout: float = 5.0):
    """Connect and read one weight reading."""
    if host is None or port is None:
        host, port = _get_config()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.settimeout(timeout)

        # Drain initial partial data
        s.settimeout(0.3)
        try:
            s.recv(4096)
        except socket.timeout:
            pass

        # Read a full line
        s.settimeout(timeout)
        buffer = b''
        while b'\n' not in buffer:
            chunk = s.recv(1)
            if not chunk:
                break
            buffer += chunk

        line = buffer.decode('ascii', errors='replace')
        return parse_weight(line)
    finally:
        s.close()
