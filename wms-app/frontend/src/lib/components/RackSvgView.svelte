<script lang="ts">
    interface Props {
        racks: any[];
        sections: any[];
        selectedRack: string;
        mode?: 'front' | 'side';
        onpalletdrop?: (palletId: string, targetAddressId: string) => void;
    }
    let { racks, sections, selectedRack, mode = 'front', onpalletdrop }: Props = $props();

    // Minimal: just show data
    let activeRack = $derived(racks.find((r: any) => r.id === selectedRack));

    function rackSections(rackId: string) {
        return sections.filter((s: any) => s.rack_id === rackId);
    }
</script>

<div style="padding:16px;background:#fff;min-height:300px;overflow:auto;flex:1">
    {#if !activeRack}
        <p class="text-gray-400">Выберите стеллаж слева</p>
    {:else}
        <h3 style="font-weight:bold;font-size:16px;margin-bottom:8px">
            {activeRack.name || activeRack.code}
            ({activeRack.sectionsCount} секций, {activeRack.floors?.length || 0} этажей)
        </h3>

        <p style="margin-bottom:8px;color:#666;font-size:13px">
            Секций на этом стеллаже: {rackSections(activeRack.id).length}
        </p>

        <div style="display:flex;flex-wrap:wrap;gap:4px">
            {#each rackSections(activeRack.id) as sec}
                <div style="padding:4px 8px;background:#e8f5e9;border:1px solid #a5d6a7;border-radius:4px;font-size:11px;font-family:monospace">
                    <div style="font-weight:bold">{sec.section_code || sec.code || '?'}</div>
                    <div style="color:#666">Э{sec.floor} {sec.typeSize_width}x{sec.typeSize_height}</div>
                    <div style="color:#333">
                        {#if sec.pallet1_code}{sec.pallet1_code}{/if}
                        {#if sec.pallet2_code}, {sec.pallet2_code}{/if}
                        {#if sec.pallet3_code}, {sec.pallet3_code}{/if}
                        {#if !sec.pallet1_code && !sec.pallet2_code && !sec.pallet3_code}
                            пусто
                        {/if}
                    </div>
                </div>
            {/each}
        </div>
    {/if}
</div>
