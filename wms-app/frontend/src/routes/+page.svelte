<script lang="ts">
    import { api } from '$lib/api';
    import RackPanel from '$lib/components/RackPanel.svelte';
    import RackSvgView from '$lib/components/RackSvgView.svelte';
    import FloorPalletBar from '$lib/components/FloorPalletBar.svelte';
    import SummaryTable from '$lib/components/SummaryTable.svelte';
    import StatsBar from '$lib/components/StatsBar.svelte';

    let warehouses: any[] = $state([]);
    let selectedWarehouse = $state('');
    let racks: any[] = $state([]);
    let sections: any[] = $state([]);
    let floorPallets: any[] = $state([]);
    let selectedRack: string = $state('');
    let viewMode: 'front' | 'side' = $state('front');
    let loading = $state(false);
    let error = $state('');

    async function loadWarehouses() {
        try {
            const data = await api.getWarehouses();
            warehouses = data.warehouses || [];
            if (warehouses.length > 0 && !selectedWarehouse) {
                selectedWarehouse = warehouses[0].id;
            }
        } catch {
            error = 'Не удалось загрузить список складов';
        }
    }

    async function loadWarehouseData() {
        if (!selectedWarehouse) return;
        loading = true; error = '';
        try {
            const [racksData, occupancyData, floorData] = await Promise.all([
                api.getRacks(selectedWarehouse),
                api.getOccupancy(selectedWarehouse),
                api.getFloor(selectedWarehouse),
            ]);
            racks = racksData.racks || [];
            sections = occupancyData.sections || [];
            floorPallets = floorData.floorPallets || [];
            selectedRack = racks[0]?.id || '';
        } catch {
            error = 'Не удалось загрузить данные склада';
        } finally {
            loading = false;
        }
    }

    $effect(() => { loadWarehouses(); });
    $effect(() => { if (selectedWarehouse) loadWarehouseData(); });
</script>

<div class="flex flex-col h-full">
    <!-- Toolbar -->
    <div class="bg-white border-b px-4 py-2 flex items-center gap-3 flex-wrap">
        <select bind:value={selectedWarehouse}
                class="border rounded px-3 py-1.5 text-sm bg-white">
            <option value="">-- Выберите склад --</option>
            {#each warehouses as w}
                <option value={w.id}>{w.name}</option>
            {/each}
        </select>

        <div class="flex gap-1 bg-gray-100 rounded p-0.5">
            <button onclick={() => viewMode = 'front'}
                    class="px-3 py-1 rounded text-sm {viewMode === 'front' ? 'bg-white shadow font-medium' : ''}">
                Вид спереди
            </button>
            <button onclick={() => viewMode = 'side'}
                    class="px-3 py-1 rounded text-sm {viewMode === 'side' ? 'bg-white shadow font-medium' : ''}">
                Вид сбоку
            </button>
        </div>

        <span class="text-xs text-gray-400 ml-auto">
            {racks.length} стеллажей · {sections.length} секций
        </span>
    </div>

    {#if error}
        <div class="bg-red-50 border-b border-red-200 px-4 py-2 text-red-700 text-sm">{error}</div>
    {/if}

    {#if loading}
        <div class="flex items-center justify-center py-20 text-gray-400">Загрузка...</div>
    {:else if racks.length > 0}
        <div class="flex flex-1 overflow-hidden">
            <!-- Rack panel (left) -->
            <RackPanel {racks} {selectedRack} onselect={(id: string) => selectedRack = id} />

            <!-- Main view -->
            <div class="flex-1 flex flex-col overflow-auto">
                <RackSvgView racks={racks} sections={sections}
                             selectedRack={selectedRack} mode={viewMode} />

                <FloorPalletBar pallets={floorPallets} racks={racks} />

                <SummaryTable sections={sections} racks={racks} />
            </div>
        </div>

        <StatsBar sections={sections} racks={racks} />
    {:else}
        <div class="flex items-center justify-center py-20 text-gray-400">
            {selectedWarehouse ? 'Нет данных' : 'Выберите склад'}
        </div>
    {/if}
</div>
