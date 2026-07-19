<script lang="ts">
    import { sectionTypeSize } from '$lib/occupancy';

    interface Props {
        racks: any[];
        sections: any[];
        selectedRack: string;
        mode?: 'front' | 'side';
        onpalletdrop?: (palletId: string, targetAddressId: string) => void;
    }
    let { racks, sections, selectedRack, mode = 'front', onpalletdrop }: Props = $props();

    // Zoom (референс: 0-1000, localStorage 'zs_scale')
    let zs = $state(typeof window !== 'undefined' ? parseInt(localStorage.getItem('zs_scale') || '100') : 100);
    let tip = $state({ show: false, x: 0, y: 0, text: '' });

    function applyScale(v: number, save = true) {
        zs = Math.max(0, Math.min(1000, v));
        if (save && typeof window !== 'undefined') localStorage.setItem('zs_scale', String(zs));
    }

    // Tooltip
    function showTip(e: MouseEvent, text: string) { tip = { show: true, x: e.clientX + 12, y: e.clientY - 10, text }; }
    function hideTip() { tip = { ...tip, show: false }; }

    // Drag & Drop
    function dragPallet(e: DragEvent) {
        const g = (e.currentTarget as HTMLElement)?.getAttribute('data-pallet-guid');
        if (!g) return;
        e.dataTransfer!.setData('text/plain', g);
        e.dataTransfer!.effectAllowed = 'move';
    }
    function dragOverCell(e: DragEvent) { e.preventDefault(); e.dataTransfer!.dropEffect = 'move'; (e.currentTarget as HTMLElement)?.classList.add('drag-over'); }
    function dragLeaveCell(e: DragEvent) { (e.currentTarget as HTMLElement)?.classList.remove('drag-over'); }
    function dropOnCell(e: DragEvent) {
        e.preventDefault();
        (e.currentTarget as HTMLElement)?.classList.remove('drag-over');
        const pg = e.dataTransfer!.getData('text/plain');
        const cg = (e.currentTarget as HTMLElement)?.getAttribute('data-cell-guid');
        if (pg && cg && onpalletdrop) onpalletdrop(pg, cg);
    }

    let activeRack = $derived(racks.find((r: any) => r.id === selectedRack));

    function floorSections(rackId: string, floorNum: number) {
        return sections.filter((s: any) =>
            (s.rack_id === rackId || s.rack === rackId) && s.floor === floorNum
        );
    }

    function palletData(sec: any, addrIdx: number) {
        const code = sec?.[`pallet${addrIdx + 1}_code`];
        if (!code) return null;
        return {
            guid: sec?.[`pallet${addrIdx + 1}_id`] || '',
            code,
            w: sec?.[`pallet${addrIdx + 1}_width`] || 0,
            h: sec?.[`pallet${addrIdx + 1}_height`] || 0,
            d: sec?.[`pallet${addrIdx + 1}_depth`] || 0,
            weight: sec?.[`pallet${addrIdx + 1}_weight`] || 0,
        };
    }

    function tipText(p: NonNullable<ReturnType<typeof palletData>>) {
        return `${p.code}\n${p.w}×${p.d}×${p.h}\n${p.weight} кг`;
    }
</script>

{#if activeRack}
    <div class="flex flex-col flex-1 min-h-0">
        <!-- Zoom toolbar -->
        <div class="scale-container">
            <div class="scale-controls">
                <button class="zoom-btn" onclick={() => applyScale(zs - 10)}>−</button>
                <span class="sc-rack">{activeRack.name || activeRack.code} ({activeRack.sectionsCount} секций)</span>
                <div class="sc-ruler-wrap">
                    <input type="range" min="0" max="1000" value={zs}
                           oninput={(e) => applyScale(parseInt(e.currentTarget.value))} />
                </div>
                <span class="scale-val">{zs}%</span>
                <button class="zoom-btn" onclick={() => applyScale(zs + 10)}>+</button>
            </div>
        </div>

        <!-- Rack grid -->
        <div class="flex-1 overflow-auto p-4" style="min-height:300px;background:#fafafa;">
            <div id="sc" style="transform:scale({zs / 100});transform-origin:top left;">
                <div class="racking">
                    {#each (activeRack.floors || []) as floor, fi}
                        {@const fs = floorSections(activeRack.id, floor.number)}
                        <div class="floor">
                            <div class="floor-side">
                                <span class="floor-header" style="font-size:{Math.max(7, 11 / (zs / 100))}px">Э{floor.number}</span>
                            </div>
                            <div class="floor-content">
                                <div class="sections-row" style="border-width:{Math.max(1, 2 / (zs / 100))}px">
                                    {#each Array(activeRack.sectionsCount || 17) as _, si}
                                        {@const sec = fs[si]}
                                        {@const ts = sec ? sectionTypeSize(sec) : (floor.typeSize || { width: 2700, height: 2100 })}
                                        {@const wPx = Math.max(60, (ts.width || 2700) / 2700 * 180)}
                                        <div class="section" style="width:{wPx}px">
                                            <!-- Header -->
                                            <div class="section-hdr" style="height:{Math.max(10, 18 / (zs / 100))}px;font-size:{Math.max(5, 7 / (zs / 100))}px">
                                                {sec?.section_code || `${si + 1}`}
                                            </div>
                                            <!-- Cells -->
                                            <div class="cells-row" style="height:{Math.max(24, (ts.height || 2100) / 2700 * 90)}px">
                                                {#each Array(3) as _, addrIdx}
                                                    {@const pallet = sec ? palletData(sec, addrIdx) : null}
                                                    {@const cellW = (wPx / 3)}
                                                    <div class="cell {!pallet ? 'cell-free' : ''}"
                                                         style="width:{cellW}px;border-width:{Math.max(0.5, 1 / (zs / 100))}px"
                                                         data-cell-guid={sec?.[`address${addrIdx + 1}`] || `${sec?.section_id || '?'}_a${addrIdx + 1}`}
                                                         ondragover={dragOverCell}
                                                         ondragleave={dragLeaveCell}
                                                         ondrop={dropOnCell}
                                                         role="button"
                                                         tabindex={pallet ? -1 : 0}>
                                                        {#if pallet}
                                                            <div class="pallet-block"
                                                                 style="height:{(pallet.h / (ts.height || 2100)) * 100}%;
                                                                        width:{(pallet.w / (ts.width || 2700)) * 100}%;
                                                                        left:{((1 - (pallet.w / (ts.width || 2700))) / 2) * 100}%;
                                                                        font-size:{Math.max(5, 9 / (zs / 100))}px"
                                                                 draggable="true"
                                                                 data-pallet-guid={pallet.guid}
                                                                 ondragstart={dragPallet}
                                                                 onmousemove={(e) => showTip(e, tipText(pallet))}
                                                                 onmouseout={hideTip}>
                                                                <span class="pallet-code">{pallet.code}</span>
                                                            </div>
                                                        {/if}
                                                    </div>
                                                {/each}
                                            </div>
                                        </div>
                                        <!-- Post -->
                                        <div class="rack-post" style="width:{Math.max(1, 2 / (zs / 100))}px"></div>
                                    {/each}
                                </div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        </div>
    </div>

    <!-- Tooltip -->
    {#if tip.show}
        <div class="pallet-tip" style="left:{tip.x}px;top:{tip.y}px">
            {#each tip.text.split('\n') as line}{line}<br />{/each}
        </div>
    {/if}

{:else}
    <div class="flex items-center justify-center h-full text-gray-400">Выберите стеллаж слева</div>
{/if}

<style>
    .scale-container{display:flex;align-items:center;gap:8px;font-size:12px;background:#fff;padding:4px 10px;border-bottom:1px solid #ccc}
    .scale-controls{display:flex;align-items:center;gap:6px;flex:1;min-width:0}
    .sc-rack{font-weight:bold;color:#333;white-space:nowrap;flex:1;min-width:0;text-align:left;padding-right:8px;overflow:hidden;text-overflow:ellipsis}
    .sc-ruler-wrap{display:flex;flex:1;min-width:0;align-items:center}
    .sc-ruler-wrap input[type=range]{width:100%;margin:0}
    .scale-val{font-weight:bold;min-width:36px;color:#333;font-size:12px}
    .zoom-btn{display:inline-block;width:28px;height:32px;line-height:30px;text-align:center;font-size:18px;font-weight:bold;color:#3a2010;background:linear-gradient(180deg,#e8c98a 0%,#d4a56a 40%,#b8863e 100%);border:1px solid #8b6914;border-radius:4px;cursor:pointer;box-shadow:0 1px 0 #f0d8a0 inset,0 -1px 2px rgba(0,0,0,.15)}
    .zoom-btn:hover{background:linear-gradient(180deg,#f0d8a0 0%,#ddb57a 40%,#c49454 100%)}

    .racking{margin-bottom:25px}
    .floor{margin-bottom:0;display:flex;align-items:stretch}
    .floor-side{width:25px;position:relative;flex-shrink:0}
    .floor-header{position:absolute;left:2px;top:2px;width:21px;text-align:center;z-index:10;font-weight:bold;color:#000}
    .floor-content{flex:1}
    .sections-row{display:flex;align-items:stretch;border:2px solid #444;width:max-content}
    .rack-post{width:2px;background:#1a4fb4;flex-shrink:0}
    .section{position:relative;box-sizing:border-box;flex:0 0 auto;display:flex;flex-direction:column}
    .section-hdr{display:flex;align-items:center;justify-content:center;background:#fff;border-bottom:1px solid #ccc;font-weight:bold;color:#000;overflow:hidden;white-space:nowrap}
    .cells-row{display:flex;width:100%;flex:1}
    .cell{position:relative;border-left:1px dashed #ccc;display:flex;flex-direction:column;justify-content:flex-end;background:rgba(200,230,200,.3)}
    .cell:first-child{border-left:none}
    .cell.cell-free{cursor:move}
    .cell:hover{background:rgba(200,230,200,.5)}
    .cell.drag-over{background:rgba(76,175,80,.45)!important}

    .pallet-block{position:absolute;bottom:0;z-index:3;background:linear-gradient(180deg,#e8c98a 0%,#d4a56a 30%,#c49454 70%,#a07030 100%);border:1px solid #8b6914;border-radius:2px;cursor:grab;display:flex;align-items:center;justify-content:center;color:#3a2010;overflow:hidden}
    .pallet-block:active{cursor:grabbing}
    .pallet-code{font-weight:bold;line-height:1.1;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

    .pallet-tip{position:fixed;z-index:9999;background:#fffde7;border:1px solid #c0a040;padding:4px 8px;font-size:11px;font-family:monospace;white-space:nowrap;pointer-events:none;box-shadow:2px 2px 6px rgba(0,0,0,.2);border-radius:3px}
</style>
