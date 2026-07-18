<script lang="ts">
    import '../app.css';
    let { children } = $props();
</script>

<div class="min-h-screen flex flex-col bg-gray-50">
    <header class="bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-4 shadow-sm">
        <h1 class="text-lg font-bold text-gray-800">WMS Оптимизатор</h1>
        <nav class="flex gap-3 text-sm">
            <a href="/" class="text-blue-600 hover:text-blue-800">Склад</a>
            <a href="/connections" class="text-gray-500 hover:text-blue-600">Подключения</a>
        </nav>
    </header>
    <main class="flex-1">
        {@render children()}
    </main>
</div>
