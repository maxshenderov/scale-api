import tkinter as tk
from tkinter import ttk, scrolledtext
import json
import urllib.request
import base64

URL = "http://it-mshenderov/1ctesterp5/hs/LikoRest/API"
LOGIN = "administrator"
PASS = "224"

tests = [
    "WMS_CheckConnection",
    "WMS_GetWarehouses",
    "WMS_GetRacks",
    "WMS_GetOccupancy",
    "WMS_GetFloor",
    "WMS_FindCell",
    "WMS_ValidatePlacement",
    "WMS_MovePallet",
    "WMS_ExportSnapshot",
    "WMS_PlacePallets",
    "WMS_GenerateMockData",
]

def call(proc):
    out.insert(tk.END, f">>> {proc}\n")
    root.update()
    data = json.dumps({"ProcName": proc}).encode()
    auth = base64.b64encode(f"{LOGIN}:{PASS}".encode()).decode()
    try:
        req = urllib.request.Request(URL, data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            try:
                body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
            except:
                pass
            out.insert(tk.END, f"OK {resp.status}\n{body}\n\n")
    except Exception as e:
        out.insert(tk.END, f"ERROR: {e}\n\n")
    out.see(tk.END)

root = tk.Tk()
root.title("WMS Phase 1 – Тестер")
root.geometry("800x650")

# Buttons frame
bf = ttk.Frame(root, padding=5)
bf.pack(fill=tk.X)
ttk.Label(bf, text=URL).pack(anchor=tk.W)
for t in tests:
    name = t.replace("WMS_", "")
    btn = ttk.Button(bf, text=name, command=lambda t=t: call(t))
    btn.pack(side=tk.LEFT, padx=2, pady=2)

# Output
out = scrolledtext.ScrolledText(root, font=("Consolas", 11))
out.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

root.mainloop()
