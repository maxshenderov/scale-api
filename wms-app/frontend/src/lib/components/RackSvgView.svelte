<script lang="ts">
    interface Props {
        racks: any[];
        sections: any[];
        selectedRack: string;
        onpalletdrop?: (palletId: string, targetAddressId: string) => void;
    }
    let { racks, sections, selectedRack, onpalletdrop }: Props = $props();

    // Из 1С: МасштабМм = 0.05 (1мм = 0.05px)
    const MM = 0.05;

    // Zoom (из 1С: localStorage 'zs_scale', 0-1000)
    let zs = $state(typeof window !== 'undefined' ? parseInt(localStorage.getItem('zs_scale') || '100') : 100);
    let tip = $state({ show: false, x: 0, y: 0, text: '' });

    function applyScale(v: number, save = true) {
        zs = Math.max(0, Math.min(1000, v));
        if (save && typeof window !== 'undefined') localStorage.setItem('zs_scale', String(zs));
    }
    function showTip(e: MouseEvent, txt: string) { tip = { show: true, x: e.clientX + 12, y: e.clientY - 10, text: txt }; }
    function hideTip() { tip = { ...tip, show: false }; }

    // Drag & Drop (из 1С — data-pallet-guid / data-cell-guid)
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

    function rackSections(rackId: string): any[] {
        return sections.filter((s: any) => (s.rack_id === rackId || s.rack === rackId));
    }

    function floorSections(rackId: string, floorNum: number): any[] {
        return rackSections(rackId).filter((s: any) => s.floor === floorNum);
    }

    // Паллет в ячейке секции по индексу (0/1/2)
    function palletAt(sec: any, idx: number): any | null {
        const code = sec?.[`pallet${idx + 1}_code`];
        if (!code) return null;
        return {
            guid: sec[`pallet${idx + 1}_id`] || '',
            code,
            w: sec[`pallet${idx + 1}_width`] || 0,
            h: sec[`pallet${idx + 1}_height`] || 0,
            d: sec[`pallet${idx + 1}_depth`] || 0,
            weight: sec[`pallet${idx + 1}_weight`] || 0,
        };
    }

    // Позиция паллета в мм от левого края секции (из 1С: строки 2126-2134)
    function palletLeftMM(sec: any, idx: number, sectionW: number): number {
        const p = palletAt(sec, idx);
        const pw = p ? p.w : 0;
        const nAddr = 3; // всегда 3 адреса
        if (nAddr === 1) return (sectionW - pw) / 2;
        if (idx === 0) return 0;
        if (idx === nAddr - 1) return sectionW - pw;
        return (sectionW - pw) / 2; // средний
    }

    function tipText(p: any): string {
        return `${p.code}\n${p.w}×${p.d}×${p.h}\n${p.weight} кг`;
    }
</script>

{#if activeRack}
    <div class="flex flex-col flex-1 min-h-0">
        <!-- Zoom toolbar (из 1С) -->
        <div class="scale-bar">
            <button class="zoom-btn" onclick={() => applyScale(zs - 10)}>−</button>
            <span class="sc-title">{activeRack.name || activeRack.code} ({activeRack.sectionsCount} секций)</span>
            <div class="sc-slider-wrap">
                <input type="range" id="zs_range" min="0" max="1000" value={zs}
                       oninput={(e) => applyScale(parseInt(e.currentTarget.value))} />
            </div>
            <span class="scale-val" id="sv">{zs}%</span>
            <button class="zoom-btn" onclick={() => applyScale(zs + 10)}>+</button>
        </div>

        <!-- Рендер стеллажа (как в 1С) -->
        <div class="flex-1 overflow-auto p-4">
            <div id="sc" style="transform:scale({zs / 100});transform-origin:top left;">
                <div class="racking">
                    {#each [...(activeRack.floors || [])].sort((a: any, b: any) => b.number - a.number) as floor}
                        {@const ts = floor.typeSize || {width: 2700, height: 2100}}
                        <!-- ВысотаЭтажаPx = Окр(Высота * 0.05, 1) -->
                        {@const hPx = Math.round((ts.height || 2100) * MM * 10) / 10}
                        <!-- ШиринаСекцииPx = Окр(Ширина * 0.05, 1) -->
                        {@const wPx = Math.round((ts.width || 2700) * MM * 10) / 10}
                        {@const fs = floorSections(activeRack.id, floor.number)}

                        <div class="floor">
                            <!-- Левая колонка (из 1С: floor-side) -->
                            <div class="floor-side">
                                <div class="floor-hdr"><span class="floor-label">Э{floor.number}</span></div>
                                <div class="floor-load">
                                    <div class="fl-l1">{ts.height || 0} мм</div>
                                    <div class="fl-l2">{ts.width || 0} мм</div>
                                </div>
                            </div>

                            <!-- Секции (из 1С: floor-content) -->
                            <div class="floor-content">
                                <!-- Шапки секций (из 1С: отдельный ряд) -->
                                <div style="display:flex;align-items:stretch;margin-bottom:0">
                                    <div class="rack-post"></div>
                                    {#each Array(activeRack.sectionsCount || 17) as _, si}
                                        {@const sec = fs[si]}
                                        <div class="rack-post"></div>
                                        <div class="section-hdr" style="width:{wPx}px">
                                            <div class="section-hdr-name">
                                                <span>{sec?.section_code || '—'}</span>
                                            </div>
                                            <div class="section-hdr-cells">
                                                <div>{sec?.address1 || ''}</div>
                                                <div>{sec?.address2 || ''}</div>
                                                <div>{sec?.address3 || ''}</div>
                                            </div>
                                        </div>
                                    {/each}
                                    <div class="rack-post"></div>
                                </div>

                                <!-- Ряд секций (из 1С: sections-row) -->
                                <div class="sections-row">
                                    <div class="rack-post"></div>
                                    {#each Array(activeRack.sectionsCount || 17) as _, si}
                                        {@const sec = fs[si]}
                                        <div class="section" style="width:{wPx}px;height:{hPx}px">
                                            <div class="cells-container">
                                                {#each Array(3) as _, ai}
                                                    {@const pallet = sec ? palletAt(sec, ai) : null}
                                                    {@const cellGuid = sec?.[`address${ai + 1}`] || ''}
                                                    <div
                                                        class="cell {!pallet ? 'cell-free' : ''}"
                                                        data-cell-guid={cellGuid}
                                                        ondragover={dragOverCell}
                                                        ondragleave={dragLeaveCell}
                                                        ondrop={dropOnCell}
                                                        role="button"
                                                        tabindex={pallet ? -1 : 0}
                                                    >
                                                        {#if pallet}
                                                            {@const pWpx = Math.round(pallet.w * MM * 10) / 10}
                                                            {@const pHpx = Math.round(pallet.h * MM * 10) / 10}
                                                            {@const leftPx = Math.round(palletLeftMM(sec!, ai, ts.width || 2700) * MM * 10) / 10}
                                                            <div
                                                                class="pallet pallet-occupied"
                                                                data-pallet-guid={pallet.guid}
                                                                draggable="true"
                                                                ondragstart={dragPallet}
                                                                onmousemove={(e) => showTip(e, tipText(pallet))}
                                                                onmouseout={hideTip}
                                                                style="left:{leftPx}px;width:{pWpx}px;height:{pHpx}px"
                                                            >
                                                                <div class="pallet-label">{pallet.code}</div>
                                                                <div class="pallet-size">{pallet.w}×{pallet.d}×{pallet.h}</div>
                                                            </div>
                                                        {/if}
                                                    </div>
                                                {/each}
                                            </div>
                                        </div>
                                        <div class="rack-post"></div>
                                    {/each}
                                </div>
                                <!-- Ширина этажа -->
                                <div class="floor-width"><div>{ts.width || 0} мм</div></div>
                            </div>
                        </div>
                    {/each}
                </div>
            </div>
        </div>
    </div>

    <!-- Тултип (из 1С) -->
    {#if tip.show}
        <div class="pallet-tooltip" style="left:{tip.x}px;top:{tip.y}px">
            {#each tip.text.split('\n') as line}{line}<br />{/each}
        </div>
    {/if}
{:else}
    <div class="flex items-center justify-center h-full text-gray-400">Выберите стеллаж слева</div>
{/if}

<style>
    /* === CSS из 1С (РендерЗагрузкиСтеллажаНаСервереЧистая) === */
    .scale-bar{display:flex;align-items:center;gap:8px;font-size:12px;background:#fff;padding:4px 10px;border-bottom:1px solid #ccc;flex-shrink:0}
    .sc-title{font-weight:bold;color:#333;white-space:nowrap;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
    .sc-slider-wrap{display:flex;flex:1;min-width:0;align-items:center}
    .sc-slider-wrap input{width:100%;margin:0}
    .scale-val{font-weight:bold;min-width:40px;color:#333;font-size:12px}
    .zoom-btn{display:inline-block;width:28px;height:32px;line-height:30px;text-align:center;font-size:18px;font-weight:bold;color:#3a2010;background:linear-gradient(180deg,#e8c98a 0%,#d4a56a 40%,#b8863e 100%);border:1px solid #8b6914;border-radius:4px;cursor:pointer;box-shadow:0 1px 0 #f0d8a0 inset,0 -1px 2px rgba(0,0,0,.15)}
    .zoom-btn:hover{background:linear-gradient(180deg,#f0d8a0 0%,#ddb57a 40%,#c49454 100%)}

    /* Стеллаж */
    .racking{margin-bottom:25px}
    .floor{display:flex;align-items:stretch}
    .floor-side{width:25px;position:relative;flex-shrink:0}
    .floor-hdr{position:absolute;left:2px;top:2px;width:21px;text-align:center;z-index:10}
    .floor-label{font-weight:bold;font-size:11px;color:#000;display:block}
    .floor-load{position:absolute;left:0;top:50%;transform:translateY(-50%) rotate(180deg);width:100%;color:#00c;font-weight:bold;writing-mode:vertical-rl;text-orientation:mixed;z-index:10;display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1.2}
    .fl-l1{font-size:7px;white-space:nowrap}
    .fl-l2{font-size:6px;white-space:nowrap}
    .floor-content{flex:1}
    .floor-width{font-size:10px;color:#333;text-align:center;margin-top:2px;font-weight:bold;display:flex}
    .sections-row{display:flex;align-items:stretch;border:2px solid #444;width:max-content;overflow:visible}
    .rack-post{width:2px;background:#1a4fb4;flex-shrink:0;box-sizing:border-box}
    .section{position:relative;box-sizing:border-box;flex:0 0 auto;display:flex;flex-direction:column;overflow:visible}
    .section-hdr{flex:0 0 auto;display:flex;flex-direction:column;overflow:hidden;background:#fff;border-bottom:1px solid #ccc;line-height:1}
    .section-hdr-name{display:flex;justify-content:space-between;align-items:center;font-size:7px;font-weight:bold;color:#000;white-space:nowrap;overflow:hidden;padding:0 2px}
    .section-hdr-cells{display:flex;width:100%}
    .section-hdr-cells>div{flex:1;text-align:center;font-size:5px;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 1px;border-left:1px solid #ccc;line-height:1}
    .section-hdr-cells>div:first-child{border-left:none}
    .cells-container{display:flex;width:100%;flex:1;align-items:stretch}
    .cell{flex:1;position:relative;border-left:1px dashed #ccc;display:flex;flex-direction:column;justify-content:flex-end;min-height:0}
    .cell:first-child{border-left:none}
    .cell-free{background:rgba(180,230,180,.45)}
    .cell-free:hover{background:rgba(100,200,100,.55);outline:2px solid #2a9a2a;outline-offset:-2px;cursor:pointer;z-index:5}
    .pallet{position:absolute;bottom:0;box-sizing:border-box;overflow:visible;z-index:2;cursor:pointer}
    .pallet:hover{outline:2px solid #ff6600;outline-offset:-2px;z-index:6;box-shadow:inset 0 0 8px rgba(255,100,0,.45)}
    .pallet-tooltip{display:none;position:fixed;background:rgba(26,79,180,.94);color:#fff;font-size:9px;font-weight:bold;padding:4px 8px;white-space:pre-line;text-align:center;z-index:200;pointer-events:none;border-radius:8px;line-height:1.3;box-shadow:0 2px 8px rgba(0,0,0,.25)}
    .pallet-occupied{background:rgba(255,255,200,.7);border:2px solid #888}
    .pallet-label{position:absolute;top:4px;left:0;right:0;text-align:center;font-size:9px;font-weight:bold;color:#333;z-index:3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .pallet-size{position:absolute;bottom:2px;left:0;right:0;text-align:center;font-size:8px;color:#333;z-index:3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

    /* Drag & Drop */
    .cell-free.drag-over{background:rgba(255,200,50,.6)!important;outline:3px solid #ff8800;outline-offset:-3px;z-index:10}
    [draggable="true"]{cursor:grab}
    [draggable="true"]:active{cursor:grabbing;opacity:.7}

    /* Svelte override for tooltip visibility */
    .pallet-tooltip[style*="display:block"] { display: block !important; }
</style>
