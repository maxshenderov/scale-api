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
        } catch (e) {
            error = 'Не удалось загрузить данные склада';
            console.error('loadWarehouseData error:', e);
        } finally {
            loading = false;
        }
    }

    async function handlePalletDrop(palletId: string, targetAddressId: string) {
        if (!selectedWarehouse) return;
        error = '';
        try {
            // 1. Валидация размещения
            const validateResult = await api.validate({
                warehouse: selectedWarehouse,
                cell: targetAddressId,
                pallet: palletId
            });

            if (validateResult.ok === false) {
                const errMsg = validateResult?.errors?.join('; ')
                    || validateResult?.error?.message
                    || validateResult?.error
                    || 'Размещение невозможно';
                error = `Невозможно разместить: ${errMsg}`;
                console.error('Validation failed:', validateResult);
                return;
            }

            // 2. Перемещение
            const moveResult = await api.move({
                warehouse: selectedWarehouse,
                pallet: palletId,
                targetCell: targetAddressId
            });

            if (moveResult.ok === false) {
                error = `Ошибка перемещения: ${moveResult?.error?.message || moveResult?.error || 'неизвестная'}`;
                console.error('Move failed:', moveResult);
                return;
            }

            // 3. Перезагрузка данных (не optimistic update — ждём подтверждения от 1С)
            await loadWarehouseData();
        } catch (e) {
            error = `Ошибка drag-and-drop: ${e instanceof Error ? e.message : String(e)}`;
            console.error('handlePalletDrop error:', e);
        }
    }

    $effect(() => { loadWarehouses(); });
    $effect(() => { if (selectedWarehouse) loadWarehouseData(); });
</script>

<div class="flex flex-col flex-1 min-h-0">
    <!-- Toolbar -->
    <div class="bg-white border-b px-4 py-3 flex flex-col gap-3">
        <!-- Warehouse selection (prominent) -->
        <div class="flex items-center gap-3">
            <label for="warehouse-select" class="text-sm font-medium text-gray-700">Склад:</label>
            <select id="warehouse-select" bind:value={selectedWarehouse}
                    class="border border-gray-300 rounded-md px-4 py-2 text-base bg-white shadow-sm
                           hover:border-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500
                           min-w-[200px]">
                <option value="">-- Выберите склад --</option>
                {#each warehouses as w}
                    <option value={w.id}>{w.name}</option>
                {/each}
            </select>
        </div>

        <!-- View mode controls -->
        <div class="flex items-center gap-3">
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
                             selectedRack={selectedRack} mode={viewMode}
                             onpalletdrop={handlePalletDrop} />

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
