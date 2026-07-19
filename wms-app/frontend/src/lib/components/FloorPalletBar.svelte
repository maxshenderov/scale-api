<script lang="ts">
    import { floorPalletLabel, floorPalletDims } from '$lib/occupancy';

    let { pallets, racks }: { pallets: any[]; racks: any[] } = $props();

    function onDragStart(event: DragEvent, fp: any) {
        // ID паллета — берём из вложенного pallet.id или из code адреса (fallback)
        const palletId = fp?.pallet?.id || fp?.address || fp?.code || '';
        event.dataTransfer?.setData('palletId', palletId);
        event.dataTransfer?.setData('text/plain', floorPalletLabel(fp));
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = 'move';
        }
    }
</script>

{#if pallets.length > 0}
    <div class="border-t bg-white px-4 py-2">
        <div class="text-xs font-medium text-gray-400 mb-1">
            Паллеты на полу <span class="text-gray-300">(перетащите на свободный адрес)</span>
        </div>
        <div class="flex gap-2 flex-wrap">
            {#each pallets as fp}
                {@const dims = floorPalletDims(fp)}
                <div draggable="true"
                     role="button"
                     tabindex="0"
                     aria-label="Паллет {floorPalletLabel(fp)} — перетащите на свободный адрес"
                     ondragstart={(e) => onDragStart(e, fp)}
                     class="px-2 py-1 bg-blue-50 border border-blue-200 rounded text-xs
                            font-mono text-blue-700 cursor-move hover:bg-blue-100
                            hover:border-blue-300 active:opacity-70 select-none">
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
