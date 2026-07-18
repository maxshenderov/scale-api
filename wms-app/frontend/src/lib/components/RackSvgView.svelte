<script lang="ts">
    import { countOccupied } from '$lib/occupancy';

    let { racks, sections, selectedRack, mode }: {
        racks: any[]; sections: any[]; selectedRack: string; mode: 'front' | 'side';
    } = $props();

    const RACK_COLORS = [
        '#E8C98A','#A8D8B9','#B8D4E3','#F0C8C8','#D5C4E1',
        '#F5DEB3','#C8E6E6','#E8D5B7','#D4E4C8'
    ];

    let activeRack = $derived(racks.find(r => r.id === selectedRack));

    function sectionsForRackFloor(rackId: string, floorNumber: number) {
        return sections.filter(s =>
            (s.rack === rackId || s.rack_id === rackId) &&
            (s.floor === undefined || s.floor === floorNumber));
    }

    function floorColor(occupied: number): string {
        if (occupied === 3) return '#4CAF50';
        if (occupied === 2) return '#FFC107';
        if (occupied === 1) return '#FF9800';
        return '#E0E0E0';
    }
</script>

{#if activeRack}
    <div class="p-4 overflow-auto">
        <svg width="100%" viewBox="0 0 {Math.max((activeRack.sectionsCount || 17) * 52 + 60, 800)} {(activeRack.floors?.length || 9) * 42 + 80}"
             class="max-w-full">
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
                    {@const occupied = sec ? countOccupied(sec) : 0}
                    {@const x = 42 + si * 52}
                    <!-- Cell -->
                    <rect x={x} y={y} width="48" height="38" rx="3"
                          fill={floorColor(occupied)}
                          stroke={occupied > 0 ? '#388E3C' : '#BDBDBD'}
                          stroke-width="1" />
                    {#if occupied > 0}
                        <text x={x + 24} y={y + 24} font-size="9" fill="#fff"
                              text-anchor="middle">{occupied}/3</text>
                    {/if}
                    <!-- Post -->
                    <rect x={x + 48} y={y} width="4" height="38" fill="#90CAF9" />
                {/each}
            {/each}

            <!-- Legend -->
            <rect x="20" y={(activeRack.floors?.length || 9) * 42 + 52} width="12" height="12" fill="#E0E0E0" stroke="#BDBDBD" />
            <text x="36" y={(activeRack.floors?.length || 9) * 42 + 63} font-size="10" fill="#666">Своб</text>
            <rect x="80" y={(activeRack.floors?.length || 9) * 42 + 52} width="12" height="12" fill="#4CAF50" />
            <text x="96" y={(activeRack.floors?.length || 9) * 42 + 63} font-size="10" fill="#666">3/3</text>
            <rect x="130" y={(activeRack.floors?.length || 9) * 42 + 52} width="12" height="12" fill="#FFC107" />
            <text x="146" y={(activeRack.floors?.length || 9) * 42 + 63} font-size="10" fill="#666">2/3</text>
            <rect x="180" y={(activeRack.floors?.length || 9) * 42 + 52} width="12" height="12" fill="#FF9800" />
            <text x="196" y={(activeRack.floors?.length || 9) * 42 + 63} font-size="10" fill="#666">1/3</text>
        </svg>
    </div>
{:else}
    <div class="flex items-center justify-center h-full text-gray-400">
        Выберите стеллаж слева
    </div>
{/if}
