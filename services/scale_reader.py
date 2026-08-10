"""
Весовой парсер — читает данные с M2M WiFi-модуля весов СКУ I2121 (СКИ-12/Yaohua).

Настройки модуля (192.168.12.147):
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

import socket
import re
import time
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


def read_weight(host: str = '192.168.12.147', port: int = 8899, timeout: float = 5.0):
    """Connect and read one weight reading."""
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


def stream_weights(host: str = '192.168.12.147', port: int = 8899):
    """Continuously stream weight readings."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect((host, port))

    buffer = b''
    print(f'Connected to {host}:{port}. Reading weights... (Ctrl+C to stop)')
    print()

    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                text = line.decode('ascii', errors='replace').strip()
                if text:
                    w = parse_weight(text)
                    if w:
                        yield w
    except KeyboardInterrupt:
        pass
    finally:
        s.close()


if __name__ == '__main__':
    print('Single reading:')
    w = read_weight()
    if w:
        print(f"  Weight: {w.value} {w.unit} ({'stable' if w.stable else 'unstable'}, {w.mode})")
        print(f"  Raw: {w.raw}")
    else:
        print('  No reading')

    print()
    print('Streaming (press Ctrl+C to stop):')
    for reading in stream_weights():
        print(f"  {reading.value:>8.1f} {reading.unit}  [{'STABLE' if reading.stable else '?'}]")
