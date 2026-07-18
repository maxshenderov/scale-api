// Нормализация занятости секций/паллет: бэкенд 1С отдаёт плоскую структуру
// (pallet1_id/pallet1_code, typeSize_height...), а офлайн MOCK — вложенную
// (addresses[].pallet, typeSize.height). Хелперы понимают оба формата.

export interface PalletSlot {
    code: string;
    width: number | null;
    height: number | null;
    depth: number | null;
    weight: number | null;
}

function isMockSection(sec: any): boolean {
    return Array.isArray(sec?.addresses);
}

export function sectionPallets(sec: any): PalletSlot[] {
    if (isMockSection(sec)) {
        return (sec.addresses || [])
            .filter((a: any) => a.pallet)
            .map((a: any) => ({
                code: a.palletCode ?? a.pallet,
                width: a.width, height: a.height, depth: a.depth, weight: a.weight,
            }));
    }
    const slots: PalletSlot[] = [];
    for (const n of [1, 2, 3]) {
        const code = sec?.[`pallet${n}_code`];
        if (code) {
            slots.push({
                code,
                width: sec[`pallet${n}_width`] ?? null,
                height: sec[`pallet${n}_height`] ?? null,
                depth: sec[`pallet${n}_depth`] ?? null,
                weight: sec[`pallet${n}_weight`] ?? null,
            });
        }
    }
    return slots;
}

export function countOccupied(sec: any): number {
    return sectionPallets(sec).length;
}

export function sectionTypeSize(sec: any): { height: number; width: number; depth: number; weight: number } {
    if (sec?.typeSize && typeof sec.typeSize === 'object') {
        return {
            height: sec.typeSize.height || 0,
            width: sec.typeSize.width || 0,
            depth: sec.typeSize.depth || 0,
            weight: sec.typeSize.weight || 0,
        };
    }
    return {
        height: sec?.typeSize_height || 0,
        width: sec?.typeSize_width || 0,
        depth: sec?.typeSize_depth || 0,
        weight: sec?.typeSize_weight || 0,
    };
}

// ВНИМАНИЕ: в реальном ответе WMS_GetFloor поля pallet_id/pallet_code — это
// GUID/код СТЕЛЛАЖА (ВложенныйЗапрос.Стеллаж), не паллета. Настоящий код
// паллета бэкенд сейчас не отдаёт вовсе — используем код адреса как fallback.
export function floorPalletLabel(fp: any): string {
    if (fp?.pallet?.code) return fp.pallet.code;
    return fp?.adress_code || fp?.address || fp?.code || '—';
}

export function floorPalletDims(fp: any): { width: number | null; height: number | null } {
    if (fp?.pallet?.width) return { width: fp.pallet.width, height: fp.pallet.height };
    return { width: fp?.width ?? null, height: fp?.height ?? null };
}
