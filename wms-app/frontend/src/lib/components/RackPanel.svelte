<script lang="ts">
    let { racks, selectedRack, onselect }: {
        racks: any[]; selectedRack: string;
        onselect: (id: string) => void;
    } = $props();

    const RACK_COLORS = [
        '#E8C98A','#A8D8B9','#B8D4E3','#F0C8C8','#D5C4E1',
        '#F5DEB3','#C8E6E6','#E8D5B7','#D4E4C8'
    ];
</script>

<div class="w-40 bg-white border-r overflow-y-auto flex-shrink-0 p-2">
    <div class="text-xs font-medium text-gray-400 mb-2 uppercase">Стеллажи</div>
    {#each racks as rack, i}
        <button
            onclick={() => onselect(rack.id)}
            class="w-full text-left px-3 py-2 mb-1 rounded text-sm font-medium transition
                   {rack.id === selectedRack ? 'ring-2 ring-blue-400 shadow' : 'hover:bg-gray-100'}"
            style="background:{RACK_COLORS[i % RACK_COLORS.length]}40;
                   border-left: 4px solid {RACK_COLORS[i % RACK_COLORS.length]}"
        >
            <div>{rack.name || rack.code || `Ряд ${rack.number}`}</div>
            <div class="text-xs text-gray-500">
                {rack.sectionsCount || 0} секц · {rack.floors?.length || 0} эт
            </div>
        </button>
    {/each}
</div>
