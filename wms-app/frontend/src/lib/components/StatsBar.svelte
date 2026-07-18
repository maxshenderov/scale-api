<script lang="ts">
    import { countOccupied } from '$lib/occupancy';

    let { sections, racks }: { sections: any[]; racks: any[] } = $props();

    let totalAddresses = $derived(sections.length * 3);
    let occupied = $derived(sections.reduce((sum, s) => sum + countOccupied(s), 0));
    let percent = $derived(totalAddresses > 0 ? Math.round(occupied / totalAddresses * 100) : 0);
</script>

<div class="bg-white border-t px-4 py-1.5 flex items-center gap-4 text-xs text-gray-500">
    <span>Стеллажей: <b class="text-gray-700">{racks.length}</b></span>
    <span>Секций: <b class="text-gray-700">{sections.length}</b></span>
    <span>Адресов: <b class="text-gray-700">{totalAddresses}</b></span>
    <span>Занято: <b class="text-gray-700">{occupied}</b></span>
    <span class="ml-auto">
        Загрузка: <b class="text-gray-700">{percent}%</b>
        <span class="ml-2 w-24 h-2 bg-gray-200 rounded inline-block align-middle">
            <span class="block h-2 rounded {percent > 80 ? 'bg-red-500' : percent > 50 ? 'bg-yellow-500' : 'bg-green-500'}"
                  style="width:{percent}%"></span>
        </span>
    </span>
</div>
