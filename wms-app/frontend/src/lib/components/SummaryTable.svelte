<script lang="ts">
    import { sectionTypeSize } from '$lib/occupancy';

    let { sections, racks }: { sections: any[]; racks: any[] } = $props();

    // Build summary: typeSize -> {rack -> count}
    let summary = $derived.by(() => {
        const map: Record<string, Record<string, number>> = {};
        for (const sec of sections) {
            const ts = sectionTypeSize(sec);
            const key = `${ts.height}x${ts.width}`;
            if (!map[key]) map[key] = {};
            const rackId = sec.rack || sec.rack_id || '?';
            map[key][rackId] = (map[key][rackId] || 0) + 1;
        }
        return map;
    });

    const typeKeys = $derived(Object.keys(summary).slice(0, 20));
</script>

{#if typeKeys.length > 0}
    <div class="border-t bg-white px-4 py-2 overflow-x-auto">
        <div class="text-xs font-medium text-gray-400 mb-1">Сводка типоразмеров</div>
        <table class="text-xs border-collapse">
            <thead>
                <tr>
                    <th class="text-left px-2 py-1 border-b sticky left-0 bg-white">Типоразмер</th>
                    {#each racks as r}
                        <th class="px-2 py-1 border-b text-center min-w-[48px]"
                            style="color:{['#E8C98A','#A8D8B9','#B8D4E3','#F0C8C8','#D5C4E1',
                                        '#F5DEB3','#C8E6E6','#E8D5B7','#D4E4C8'][racks.indexOf(r) % 9]}">
                            {r.code || r.number || r.name?.slice(0,4)}
                        </th>
                    {/each}
                </tr>
            </thead>
            <tbody>
                {#each typeKeys as key}
                    <tr>
                        <td class="px-2 py-1 border-b font-mono sticky left-0 bg-white">{key}</td>
                        {#each racks as r}
                            <td class="px-2 py-1 border-b text-center">
                                {summary[key]?.[r.id] || '—'}
                            </td>
                        {/each}
                    </tr>
                {/each}
            </tbody>
        </table>
    </div>
{/if}
