<script lang="ts">
    import { countOccupied, sectionPallets, sectionTypeSize } from '$lib/occupancy';

    let { racks, sections, selectedRack, mode = 'front', onpalletdrop }: {
        racks: any[]; sections: any[]; selectedRack: string;
        mode?: 'front' | 'side';
        onpalletdrop?: (palletId: string, targetAddressId: string) => void;
    } = $props();

    const RACK_COLORS = [
        '#E8C98A','#A8D8B9','#B8D4E3','#F0C8C8','#D5C4E1',
        '#F5DEB3','#C8E6E6','#E8D5B7','#D4E4C8'
    ];

    let scale = $state(typeof window !== 'undefined' ?
        parseFloat(localStorage.getItem('wms-rack-scale') || '1.0') : 1.0);

    let activeRack = $derived(racks.find(r => r.id === selectedRack));

    function sectionsForRackFloor(rackId: string, floorNumber: number) {
        return sections.filter(s =>
            (s.rack === rackId || s.rack_id === rackId) &&
            (s.floor === undefined || s.floor === floorNumber));
    }

    function tooltipText(pallet: any): string {
        return `${pallet.code || '—'}\n${pallet.width || 0}×${pallet.depth || 1100}×${pallet.height || 0}\n${pallet.weight || 0} кг`;
    }

    function palletColor(index: number): string {
        const colors = ['#4CAF50', '#FFC107', '#FF9800'];
        return colors[index % colors.length];
    }

    function updateScale(newScale: number) {
        scale = Math.max(0.5, Math.min(2.0, newScale));
        if (typeof window !== 'undefined') {
            localStorage.setItem('wms-rack-scale', scale.toString());
        }
    }

    // Возвращает GUID адреса для слота addrIndex секции
    function addressGuid(sec: any, addrIndex: number): string {
        const addresses = sec?.addresses;
        if (Array.isArray(addresses) && addresses[addrIndex]) {
            return addresses[addrIndex].address || addresses[addrIndex].addressCode || '';
        }
        // Fallback для плоского формата
        return sec?.[`address${addrIndex + 1}`] || '';
    }

    // Паллет на конкретном слоте (или null если свободно)
    function palletAtSlot(sec: any, addrIndex: number): any | null {
        const addresses = sec?.addresses;
        if (Array.isArray(addresses) && addresses[addrIndex]?.pallet) {
            const a = addresses[addrIndex];
            return {
                code: a.palletCode,
                width: a.width, height: a.height, depth: a.depth, weight: a.weight
            };
        }
        // Fallback: используем sectionPallets (только занятые)
        const pallets = sec ? sectionPallets(sec) : [];
        return pallets[addrIndex] || null;
    }

    function handleDrop(event: DragEvent, targetAddressId: string) {
        event.preventDefault();
        if (!event.dataTransfer) return;
        const palletId = event.dataTransfer.getData('palletId');
        if (palletId && targetAddressId && onpalletdrop) {
            onpalletdrop(palletId, targetAddressId);
        }
    }

    function allowDrop(event: DragEvent) {
        event.preventDefault();
        if (event.dataTransfer) {
            event.dataTransfer.dropEffect = 'move';
        }
    }
</script>

{#if activeRack}
    <div class="flex flex-col h-full">
        <!-- Zoom controls -->
        <div class="bg-white border-b px-4 py-2 flex items-center gap-3">
            <button onclick={() => updateScale(scale - 0.1)}
                    class="px-3 py-1 rounded bg-gray-200 text-sm hover:bg-gray-300">−</button>
            <input type="range" min="0.5" max="2.0" step="0.1" bind:value={scale}
                   onchange={(e) => updateScale(parseFloat(e.currentTarget.value))}
                   class="w-32" />
            <button onclick={() => updateScale(scale + 0.1)}
                    class="px-3 py-1 rounded bg-gray-200 text-sm hover:bg-gray-300">+</button>
            <span class="text-sm text-gray-600">{Math.round(scale * 100)}%</span>
        </div>

        <div class="flex-1 overflow-auto p-4">
            <div style={`transform: scale(${scale}); transform-origin: top left; display: inline-block;`}>
                <svg width="{Math.max((activeRack.sectionsCount || 17) * 52 + 60, 800)}"
                     height="{(activeRack.floors?.length || 9) * 42 + 80}"
                     class="border border-gray-300 bg-white">
                    <!-- Rack label -->
                    <text x="20" y="20" font-size="13" font-weight="bold" fill="#333">
                        {activeRack.name || activeRack.code} ({activeRack.sectionsCount} секций)
                    </text>

                    {#each (activeRack.floors || []) as floor, fi}
                        {@const y = 40 + fi * 42}
                        <!-- Floor number -->
                        <text x="10" y={y + 22} font-size="10" fill="#888">Э{floor.number}</text>

                        {@const rackSections = sectionsForRackFloor(activeRack.id, floor.number)}
                        {#each Array(activeRack.sectionsCount || 17) as _, si}
                            {@const sec = rackSections[si]}
                            {@const typeSize = sec ? sectionTypeSize(sec) : { width: 2700 }}
                            {@const sectionWidth = 48}
                            {@const x = 42 + si * 52}

                            <!-- Section background -->
                            <rect x={x} y={y} width={sectionWidth} height="38" rx="3"
                                  fill="#F5F5F5" stroke="#BDBDBD" stroke-width="1" />

                            <!-- Individual pallet slots -->
                            {#each Array(3) as _, addrIndex}
                                {@const pallet = sec ? palletAtSlot(sec, addrIndex) : null}
                                {@const addressId = sec ? addressGuid(sec, addrIndex) : ''}
                                {@const palletX = x + (addrIndex * sectionWidth / 3)}
                                {#if pallet}
                                    <rect x={palletX} y={y} width={sectionWidth / 3 - 1} height="38" rx="2"
                                          fill={palletColor(addrIndex)} stroke="#388E3C" stroke-width="1"
                                          class="cursor-pointer hover:opacity-80" />
                                    <title>{tooltipText(pallet)}</title>
                                    <text x={palletX + sectionWidth / 6} y={y + 24} font-size="7" fill="#fff"
                                          text-anchor="middle" class="pointer-events-none">{pallet.code || '—'}</text>
                                {:else}
                                    <!-- Free address (drop zone) -->
                                    <rect x={palletX} y={y} width={sectionWidth / 3 - 1} height="38" rx="2"
                                          fill="#E0E0E0" stroke="#BDBDBD" stroke-width="1" stroke-dasharray="2,2"
                                          class="cursor-move drop-zone"
                                          role="button"
                                          tabindex="0"
                                          ondragover={allowDrop}
                                          ondrop={(e) => handleDrop(e, addressId)} />
                                {/if}
                            {/each}

                            <!-- Post -->
                            <rect x={x + sectionWidth} y={y} width="4" height="38" fill="#90CAF9" />
                        {/each}
                    {/each}

                    <!-- Legend -->
                    <rect x="20" y={(activeRack.floors?.length || 9) * 42 + 52} width="12" height="12" fill="#E0E0E0" stroke="#BDBDBD" />
                    <text x="36" y={(activeRack.floors?.length || 9) * 42 + 63} font-size="10" fill="#666">Своб</text>
                    <rect x="80" y={(activeRack.floors?.length || 9) * 42 + 52} width="12" height="12" fill="#4CAF50" />
                    <text x="96" y={(activeRack.floors?.length || 9) * 42 + 63} font-size="10" fill="#666">Паллет</text>
                </svg>
            </div>
        </div>
    </div>
{:else}
    <div class="flex items-center justify-center h-full text-gray-400">
        Выберите стеллаж слева
    </div>
{/if}

<style>
    :global(.cursor-move) {
        cursor: move;
    }
    :global(.cursor-pointer) {
        cursor: pointer;
    }
    .drop-zone:hover {
        fill: #C8E6C9 !important;
        stroke: #4CAF50 !important;
    }
</style>

