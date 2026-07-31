"""
Тест для /api/pack_section — локальная оптимизация заполнения секции.
"""
import pytest

from api.schemas import PackSectionRequest, SectionConstraintsSchema, PalletTypeSchema
from api.routes import pack_section


def test_pack_section_basic():
    """Базовый тест: 3 типа, должен взять оптимальную комбинацию."""
    req = PackSectionRequest(
        section=SectionConstraintsSchema(
            width=2700,
            height=2400,
            depth=1200,
            max_pallets=3,
            max_weight=3000,
            narrow_aisle=False,
            max_width_pallet=None,
        ),
        availableTypes=[
            PalletTypeSchema(width=1200, height=2200, depth=1000, weight=800, count=5),
            PalletTypeSchema(width=900, height=2100, depth=1000, weight=600, count=10),
            PalletTypeSchema(width=800, height=2000, depth=1000, weight=500, count=15),
        ],
    )

    import asyncio
    result = asyncio.run(pack_section(req))

    # Проверки
    assert result.usedPallets <= 3
    assert result.usedPallets > 0
    assert result.usedWeight <= 3000
    assert 0 <= result.utilization <= 1

    # Лучшая комбинация с учётом зазоров (N паллет → N+1 зазоров):
    # - 3×900 = 2700 + 4×50 = 2900мм > 2700 ✗
    # - 2×1200 = 2400 + 3×50 = 2550мм < 2700 ✓
    # - 3×800 = 2400 + 4×50 = 2600мм < 2700 ✓
    # - 1×900 + 2×800 = 2500 + 4×50 = 2700мм ✓ (идеально!)
    #
    # Алгоритм должен найти либо 1×900+2×800 (3 паллеты, 100%), либо 3×800 (3 паллеты, 96%)
    total_width_no_gaps = sum(
        req.availableTypes[s.typeIndex].width * s.count
        for s in result.selected
    )
    print(f"\nРазмещено: {result.usedPallets} паллет, чистая ширина {total_width_no_gaps}мм, утилизация {result.utilization:.2%}")

    # Ожидание: 3 паллеты, утилизация >90%
    assert result.usedPallets == 3
    assert total_width_no_gaps >= 2400  # Минимум 3×800 или лучше


def test_pack_section_narrow_aisle():
    """Узкопроходный стеллаж — должен фильтровать широкие паллеты."""
    req = PackSectionRequest(
        section=SectionConstraintsSchema(
            width=2700,
            height=2400,
            depth=1200,
            max_pallets=3,
            max_weight=None,
            narrow_aisle=True,
            max_width_pallet=1000,  # Только ≤1000мм
        ),
        availableTypes=[
            PalletTypeSchema(width=1200, height=2200, depth=1000, weight=800, count=5),  # Не влезет
            PalletTypeSchema(width=900, height=2100, depth=1000, weight=600, count=10),
            PalletTypeSchema(width=800, height=2000, depth=1000, weight=500, count=15),
        ],
    )

    import asyncio
    result = asyncio.run(pack_section(req))

    # Должен взять только 900 и 800 (не 1200)
    for s in result.selected:
        assert req.availableTypes[s.typeIndex].width <= 1000

    print(f"\nУзкопроходный: {result.usedPallets} паллет, утилизация {result.utilization:.2%}")


def test_pack_section_height_limit():
    """Должен отфильтровать высокие паллеты."""
    req = PackSectionRequest(
        section=SectionConstraintsSchema(
            width=2700,
            height=2100,  # Низкая секция
            depth=1200,
            max_pallets=3,
            max_weight=None,
            narrow_aisle=False,
            max_width_pallet=None,
        ),
        availableTypes=[
            PalletTypeSchema(width=1200, height=2200, depth=1000, weight=800, count=5),  # Не влезет по высоте
            PalletTypeSchema(width=900, height=2100, depth=1000, weight=600, count=10),   # OK
            PalletTypeSchema(width=800, height=2000, depth=1000, weight=500, count=15),   # OK
        ],
    )

    import asyncio
    result = asyncio.run(pack_section(req))

    # Не должен взять 2200мм паллеты
    for s in result.selected:
        assert req.availableTypes[s.typeIndex].height <= 2100

    print(f"\nНизкая секция: {result.usedPallets} паллет")


def test_pack_section_weight_limit():
    """Должен уложиться в лимит веса."""
    req = PackSectionRequest(
        section=SectionConstraintsSchema(
            width=2700,
            height=2400,
            depth=1200,
            max_pallets=3,
            max_weight=1500,  # Жёсткий лимит веса
            narrow_aisle=False,
            max_width_pallet=None,
        ),
        availableTypes=[
            PalletTypeSchema(width=900, height=2100, depth=1000, weight=600, count=10),
        ],
    )

    import asyncio
    result = asyncio.run(pack_section(req))

    # 3×600 = 1800кг > 1500 → должен взять только 2
    assert result.usedWeight <= 1500
    assert result.usedPallets <= 2

    print(f"\nЛимит веса: {result.usedPallets} паллет, {result.usedWeight}кг")


def test_pack_section_empty_types():
    """Все типы count=0 — должен вернуть пустой результат."""
    req = PackSectionRequest(
        section=SectionConstraintsSchema(
            width=2700,
            height=2400,
            depth=1200,
            max_pallets=3,
            max_weight=None,
            narrow_aisle=False,
            max_width_pallet=None,
        ),
        availableTypes=[
            PalletTypeSchema(width=900, height=2100, depth=1000, weight=600, count=0),
            PalletTypeSchema(width=800, height=2000, depth=1000, weight=500, count=0),
        ],
    )

    import asyncio
    result = asyncio.run(pack_section(req))

    assert result.usedPallets == 0
    assert len(result.selected) == 0
    print(f"\nПустой: {result.usedPallets} паллет")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
