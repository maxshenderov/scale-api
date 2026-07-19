<script lang="ts">
    import { sectionPallets, sectionTypeSize } from '$lib/occupancy';

    interface Props {
        racks: any[];
        sections: any[];
        selectedRack: string;
        mode?: 'front' | 'side';
        onpalletdrop?: (palletId: string, targetAddressId: string) => void;
    }

    let { racks, sections, selectedRack, mode = 'front', onpalletdrop }: Props = $props();

    let activeRack = $derived(racks.find(r => r.id === selectedRack));

    // --- Zoom (референс: 0–1000, шаг 10, localStorage 'zs_scale') ---
    let zs = $state(typeof window !== 'undefined' ?
        parseInt(localStorage.getItem('zs_scale') || '100') : 100);

    let tip = $state({ show: false, x: 0, y: 0, text: '' });

    function applyScale(v: number, save: boolean = true) {
        zs = Math.max(0, Math.min(1000, v));
        if (save && typeof window !== 'undefined') {
            localStorage.setItem('zs_scale', String(zs));
        }
    }

    function zoomIn() { applyScale(zs + 10); }
    function zoomOut() { applyScale(zs - 10); }

    // --- Tooltip (референс: showTip/hideTip) ---
    function showTip(event: MouseEvent, text: string) {
        tip = { show: true, x: event.clientX + 12, y: event.clientY - 10, text };
    }
    function hideTip() { tip = { ...tip, show: false }; }

    // --- Drag & Drop (референс: data-pallet-guid, data-cell-guid, text/plain) ---
    function dragPallet(event: DragEvent) {
        const g = (event.currentTarget as HTMLElement)?.getAttribute('data-pallet-guid');
        if (!g) return;
        event.dataTransfer!.setData('text/plain', g);
        event.dataTransfer!.effectAllowed = 'move';
    }

    function dragOverCell(event: DragEvent) {
        event.preventDefault();
        event.dataTransfer!.dropEffect = 'move';
        (event.currentTarget as HTMLElement)?.classList.add('drag-over');
    }

    function dragLeaveCell(event: DragEvent) {
        (event.currentTarget as HTMLElement)?.classList.remove('drag-over');
    }

    function dropOnCell(event: DragEvent) {
        event.preventDefault();
        (event.currentTarget as HTMLElement)?.classList.remove('drag-over');
        const pg = event.dataTransfer!.getData('text/plain');
        const cg = (event.currentTarget as HTMLElement)?.getAttribute('data-cell-guid');
        if (pg && cg && onpalletdrop) {
            onpalletdrop(pg, cg);
        }
    }

    // --- Helpers ---
    function sectionsForRackFloor(rackId: string, floorNumber: number) {
        return sections.filter(s =>
            (s.rack === rackId || s.rack_id === rackId) &&
            (s.floor === undefined || s.floor === floorNumber));
    }

    function cellGuid(sec: any, addrIndex: number): string {
        return sec?.[`address${addrIndex + 1}`] || `${sec?.section_id || '?'}_addr${addrIndex + 1}`;
    }

    function palletGuid(sec: any, addrIndex: number): string {
        return sec?.[`pallet${addrIndex + 1}_id`] || '';
    }

    function palletCode(sec: any, addrIndex: number): string {
        return sec?.[`pallet${addrIndex + 1}_code`] || '';
    }

    function palletInfo(sec: any, addrIndex: number): { guid: string; code: string; w: number; h: number; d: number; weight: number } | null {
        const code = palletCode(sec, addrIndex);
        if (!code) return null;
        return {
            guid: palletGuid(sec, addrIndex),
            code,
            w: sec?.[`pallet${addrIndex + 1}_width`] || 0,
            h: sec?.[`pallet${addrIndex + 1}_height`] || 0,
            d: sec?.[`pallet${addrIndex + 1}_depth`] || 0,
            weight: sec?.[`pallet${addrIndex + 1}_weight`] || 0,
        };
    }

    function palletTip(p: NonNullable<ReturnType<typeof palletInfo>>): string {
        return `${p.code}\n${p.w}×${p.d}×${p.h}\n${p.weight} кг`;
    }
</script>

{#if activeRack}
    <div class="flex flex-col h-full">
        <!-- Zoom toolbar (референс) -->
        <div class="scale-container">
            <div class="scale-controls">
                <button class="zoom-btn" onclick={zoomOut} title="Уменьшить">−</button>
                <span class="sc-rack">{activeRack.name || activeRack.code} ({activeRack.sectionsCount} секций)</span>
                <div class="sc-ruler-wrap">
                    <input type="range" min="0" max="1000" value={zs}
                           oninput={(e) => applyScale(parseInt(e.currentTarget.value))} />
                </div>
                <span class="scale-val">{zs}%</span>
                <button class="zoom-btn" onclick={zoomIn} title="Увеличить">+</button>
            </div>
        </div>

        <!-- Rack view (scaled) -->
        <div class="flex-1 overflow-auto p-4">
            <div id="sc" style="transform:scale({zs / 100});transform-origin:top left;">
                <div class="racking">
                    {#each (activeRack.floors || []) as floor, fi}
                        <div class="floor">
                            <div class="floor-side">
                                <span class="floor-header" style="font-size:{9 / (zs / 100)}px">Э{floor.number}</span>
                            </div>
                            <div class="floor-content">
                                <div class="sections-row" style="border-width:{2 / (zs / 100)}px">
                                    {#each Array(activeRack.sectionsCount || 17) as _, si}
                                        {@const sec = sectionsForRackFloor(activeRack.id, floor.number)[si]}
                                        {@const ts = sec ? sectionTypeSize(sec) : { width: 2700, height: 2100 }}
                                        {@const wPx = Math.max(40, ts.width / 2700 * 180)}
                                        <div class="section"
                                             style="width:{wPx}px">
                                            <!-- Section header -->
                                            <div class="section-header" style="height:{18 / (zs / 100)}px">
                                                <div class="section-header-name" style="font-size:{7 / (zs / 100)}px">
                                                    {sec?.section_code || `${si + 1}`}
                                                </div>
                                                <div class="section-header-cells" style="font-size:{5 / (zs / 100)}px">
                                                    <div>А1</div><div>А2</div><div>А3</div>
                                                </div>
                                            </div>
                                            <!-- Cells -->
                                            <div class="cells-container" style="height:{Math.max(20, ts.height / 2700 * 90)}px">
                                                {#each Array(3) as _, addrIdx}
                                                    {@const pallet = sec ? palletInfo(sec, addrIdx) : null}
                                                    {@const cg = sec ? cellGuid(sec, addrIdx) : ''}
                                                    <div class="cell {!pallet ? 'cell-free' : ''}"
                                                         style="border-width:{1 / (zs / 100)}px"
                                                         data-cell-guid={cg}
                                                         ondragover={dragOverCell}
                                                         ondragleave={dragLeaveCell}
                                                         ondrop={dropOnCell}
                                                         role="button"
                                                         tabindex={pallet ? -1 : 0}>
                                                        {#if pallet}
                                                            <div class="pallet-occupied"
                                                                 style="height:{(pallet.h / (ts.height || 2100)) * 100}%;
                                                                        width:{(pallet.w / (ts.width || 2700)) * 100}%;
                                                                        left:{((1 - (pallet.w / (ts.width || 2700))) / 2) * 100}%;
                                                                        font-size:{9 / (zs / 100)}px"
                                                                 draggable="true"
                                                                 data-pallet-guid={pallet.guid}
                                                                 ondragstart={dragPallet}
                                                                 onmousemove={(e) => showTip(e, palletTip(pallet))}
                                                                 onmouseout={hideTip}>
                                                                <span class="pallet-label">{pallet.code}</span>
                                                                <span class="pallet-size">{pallet.w}×{pallet.d}×{pallet.h}</span>
                                                            </div>
                                                        {/if}
                                                    </div>
                                                {/each}
                                            </div>
                                        </div>
                                        <!-- Post -->
                                        <div class="rack-post" style="width:{2 / (zs / 100)}px"></div>
                                    {/each}
                                </div>
                                <div class="floor-width" style="font-size:{10 / (zs / 100)}px">
                                    <div data-bw={ts.width}>{ts.width} мм</div>
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        </div>
    </div>

    <!-- Tooltip (референс: showTip/hideTip) -->
    {#if tip.show}
        <div class="pallet-tip" style="left:{tip.x}px;top:{tip.y}px">
            {#each tip.text.split('\n') as line}
                {line}<br />
            {/each}
        </div>
    {/if}

{:else}
    <div class="flex items-center justify-center h-full text-gray-400">
        Выберите стеллаж слева
    </div>
{/if}

<style>
    /* === Zoom toolbar (из референса) === */
    .scale-container {
        display: flex; align-items: center; gap: 8px; font-size: 12px;
        background: #fff; padding: 4px 10px; border-bottom: 1px solid #ccc;
    }
    .scale-controls {
        display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0;
    }
    .sc-rack {
        font-weight: bold; color: #333; white-space: nowrap; flex: 1; min-width: 0;
        text-align: left; padding-right: 8px; overflow: hidden; text-overflow: ellipsis;
    }
    .sc-ruler-wrap { display: flex; flex: 1; min-width: 0; align-items: center; }
    .sc-ruler-wrap input[type=range] { width: 100%; margin: 0; display: block; }
    .scale-val { font-weight: bold; min-width: 36px; color: #333; font-size: 12px; }
    .zoom-btn {
        display: inline-block; width: 28px; height: 32px; line-height: 30px;
        text-align: center; font-size: 18px; font-weight: bold; color: #3a2010;
        background: linear-gradient(180deg, #e8c98a 0%, #d4a56a 40%, #b8863e 100%);
        border: 1px solid #8b6914; border-radius: 4px; cursor: pointer;
        box-shadow: 0 1px 0 #f0d8a0 inset, 0 -1px 2px rgba(0,0,0,0.15);
    }
    .zoom-btn:hover { background: linear-gradient(180deg, #f0d8a0 0%, #ddb57a 40%, #c49454 100%); }

    /* === Rack structure (из референса) === */
    .racking { margin-bottom: 25px; }
    .floor { margin-bottom: 0; display: flex; align-items: stretch; }
    .floor-side { width: 25px; position: relative; flex-shrink: 0; }
    .floor-header { position: absolute; left: 2px; top: 2px; width: 21px;
        text-align: center; z-index: 10; font-weight: bold; color: #000; }
    .floor-content { flex: 1; }
    .floor-width { font-size: 10px; color: #333; text-align: center; margin-top: 2px;
        font-weight: bold; display: flex; }
    .sections-row {
        display: flex; align-items: stretch; border: 2px solid #444; width: max-content;
        overflow: visible;
    }
    .rack-post { width: 2px; background: #1a4fb4; flex-shrink: 0; box-sizing: border-box; }
    .section {
        position: relative; box-sizing: border-box; flex: 0 0 auto; display: flex;
        flex-direction: column; overflow: visible;
    }
    .section-header {
        flex: 0 0 auto; display: flex; flex-direction: column; overflow: hidden;
        background: #fff; border-bottom: 1px solid #ccc; line-height: 1;
    }
    .section-header-name {
        display: flex; justify-content: space-between; align-items: center; font-weight: bold;
        color: #000; white-space: nowrap; overflow: hidden; padding: 0 2px;
    }
    .section-header-cells { display: flex; width: 100%; }
    .section-header-cells > div {
        flex: 1; text-align: center; color: #333; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis; padding: 0 1px; border-left: 1px solid #ccc;
    }
    .section-header-cells > div:first-child { border-left: none; }

    /* === Cells & Pallets === */
    .cells-container { display: flex; width: 100%; flex: 1; align-items: stretch; }
    .cell {
        flex: 1; position: relative; border-left: 1px dashed #ccc;
        display: flex; flex-direction: column; justify-content: flex-end; min-height: 0;
        background: rgba(200,230,200,0.3);
    }
    .cell:first-child { border-left: none; }
    .cell.cell-free { cursor: move; }
    .cell:hover { background: rgba(200,230,200,0.5); }
    .cell.drag-over { background: rgba(76,175,80,0.45) !important; }

    .pallet-occupied {
        position: absolute; bottom: 0; z-index: 3;
        background: linear-gradient(180deg, #e8c98a 0%, #d4a56a 30%, #c49454 70%, #a07030 100%);
        border: 1px solid #8b6914; border-radius: 2px; cursor: grab;
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        color: #3a2010; overflow: hidden; min-height: 0;
    }
    .pallet-occupied:active { cursor: grabbing; }
    .pallet-label { font-weight: bold; line-height: 1.1; text-align: center; }
    .pallet-size { line-height: 1.1; text-align: center; }

    /* === Tooltip === */
    .pallet-tip {
        position: fixed; z-index: 9999; background: #fffde7; border: 1px solid #c0a040;
        padding: 4px 8px; font-size: 11px; font-family: monospace; white-space: nowrap;
        pointer-events: none; box-shadow: 2px 2px 6px rgba(0,0,0,0.2); border-radius: 3px;
    }

    /* Global */
    :global(.drag-over) { outline: 2px solid #4CAF50; }
</style>
