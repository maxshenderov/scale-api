"""MOCK-данные для разработки и тестирования без 1С."""

MOCK_SNAPSHOT = {
    "exportedAt": "2026-07-18T12:00:00",
    "warehouse": {"id": "wh-1", "name": "ЛК Высотный (MOCK)", "code": "000000001"},
    "racks": [
        {
            "id": f"rack-{i}", "name": f"Ряд {i}", "code": str(i), "number": i,
            "narrowAisle": i <= 7,
            "color": ["#E8C98A","#A8D8B9","#B8D4E3","#F0C8C8","#D5C4E1",
                       "#F5DEB3","#C8E6E6","#E8D5B7","#D4E4C8"][i-1],
            "minPalletWidth": 0, "maxPalletWidth": 1350 if i <= 8 else 2300,
            "minPalletDepth": 800, "maxPalletDepth": 1100,
            "sectionsCount": 17, "cellsPerSection": 3, "floorZonesCount": 5,
            "floors": [
                {
                    "number": f, "typeSize": {
                        "id": f"ts-{i}-{f}", "height": [2340,2350,1550,1800,1800,1500,1500,1500,1500][f-1],
                        "width": 2700 if i != 9 else 2300, "depth": 1100,
                        "weight": 3000, "unlimitedWeight": f == 1
                    },
                    "minDepthUnlimited": True, "maxDepthUnlimited": True,
                    "maxLiftWeight": [3000,2500,2000,1800,1500,1200,1000,800,600][f-1] if f <= 9 else 500,
                    "heightClearance": 100, "beamHeight": 120 if f < 9 else 0,
                    "heightFromFloor": 200 if f == 1 else 0,
                    "heightFromFloorFixed": f == 1,
                    "widthClearance": 50
                }
                for f in range(1, 10)
            ]
        }
        for i in range(1, 10)
    ],
    "sections": [],
    "floorPallets": [
        {"rack": f"rack-{r}", "address": f"floor-addr-{r}", "code": f"ПОЛ-Приёмка-{r}",
         "pallet": {"id": f"fp-{r}", "code": f"P-{1000+r}", "width": 800, "height": 1200, "depth": 1100, "weight": 500}}
        for r in range(1, 5)
    ],
    "summary": {"racksCount": 9, "totalSections": 1403, "totalAddresses": 4209,
                 "occupiedAddresses": 2847, "occupancyPercent": 67.6}
}

# Генерируем секции с паллетами
import random, uuid
random.seed(42)

for rack_num in range(1, 10):
    for section_num in range(1, 18):
        sec_id = f"sec-{rack_num}-{section_num}"
        addresses = []
        for addr_pos in range(1, 4):
            addr_id = f"addr-{rack_num}-{section_num}-{addr_pos}"
            # ~60% занятость
            has_pallet = random.random() < 0.6
            if has_pallet:
                w = random.choice([700, 750, 800, 850, 900])
                h = random.choice([1000, 1200, 1300, 1500, 1800])
                addresses.append({
                    "address": addr_id, "addressCode": f"R{rack_num}S{section_num}A{addr_pos}",
                    "pallet": f"pallet-{rack_num}-{section_num}-{addr_pos}",
                    "palletCode": f"P-{rack_num}{section_num:02d}{addr_pos}",
                    "width": w, "height": h, "depth": 1100, "weight": random.choice([300, 500, 800])
                })
            else:
                addresses.append({
                    "address": addr_id, "addressCode": f"R{rack_num}S{section_num}A{addr_pos}",
                    "pallet": None, "palletCode": None,
                    "width": None, "height": None, "depth": None, "weight": None
                })
        MOCK_SNAPSHOT["sections"].append({
            "id": sec_id, "code": f"R{rack_num}-M({(section_num-1)*3+1}-{(section_num-1)*3+2}-{(section_num-1)*3+3})-E1",
            "rack": f"rack-{rack_num}", "rack_id": f"rack-{rack_num}",
            "floor": random.randint(1, 9), "accessLevel": 1, "restricted": False,
            "typeSize": {"width": 2700, "height": 2340, "depth": 1100, "weight": 3000, "unlimitedWeight": True},
            "addresses": addresses
        })
