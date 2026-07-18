<script lang="ts">
    import { floorPalletLabel, floorPalletDims } from '$lib/occupancy';

    let { pallets, racks }: { pallets: any[]; racks: any[] } = $props();
</script>

{#if pallets.length > 0}
    <div class="border-t bg-white px-4 py-2">
        <div class="text-xs font-medium text-gray-400 mb-1">Паллеты на полу</div>
        <div class="flex gap-2 flex-wrap">
            {#each pallets as fp}
                {@const dims = floorPalletDims(fp)}
                <div class="px-2 py-1 bg-blue-50 border border-blue-200 rounded text-xs
                            font-mono text-blue-700">
                    {floorPalletLabel(fp)}
                    {#if dims.width}
                        <span class="text-blue-400 ml-1">
                            {dims.width}&times;{dims.height}
                        </span>
                    {/if}
                </div>
            {/each}
        </div>
    </div>
{/if}
