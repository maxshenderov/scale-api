<script lang="ts">
    import { api } from '$lib/api';

    let connections = $state<any[]>([]);
    let newConn = $state({ name: '', url: '', login: '', password: '' });
    let testResult = $state('');

    async function load() {
        try { const data = await api.getConnections(); connections = data.connections || []; } catch {}
    }

    async function add() {
        await fetch('/api/connections', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newConn),
        });
        newConn = { name: '', url: '', login: '', password: '' };
        load();
    }

    async function remove(id: number) {
        await fetch(`/api/connections/${id}`, { method: 'DELETE' });
        load();
    }

    async function activate(id: number) {
        await fetch(`/api/connections/${id}/activate`, { method: 'POST' });
        load();
    }

    async function test(id: number) {
        testResult = 'Проверка...';
        try {
            const data = await fetch(`/api/connections/${id}/test`, { method: 'POST' }).then(r => r.json());
            testResult = data.ok ? 'OK: соединение работает' : 'Ошибка: ' + (data.error || 'нет ответа');
        } catch (e: any) {
            testResult = 'Ошибка: ' + e.message;
        }
    }

    $effect(() => { load(); });
</script>

<div class="max-w-2xl mx-auto p-6">
    <h2 class="text-xl font-bold mb-4">Подключения к 1С</h2>

    <!-- Add form -->
    <div class="bg-white rounded-lg border p-4 mb-4 space-y-2">
        <input class="w-full border rounded px-3 py-2 text-sm" placeholder="Название"
               bind:value={newConn.name} />
        <input class="w-full border rounded px-3 py-2 text-sm" placeholder="URL (http://server/hs/LikoRest/API)"
               bind:value={newConn.url} />
        <div class="flex gap-2">
            <input class="flex-1 border rounded px-3 py-2 text-sm" placeholder="Логин"
                   bind:value={newConn.login} />
            <input class="flex-1 border rounded px-3 py-2 text-sm" placeholder="Пароль"
                   bind:value={newConn.password} type="password" />
        </div>
        <button onclick={add} class="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700">
            Добавить
        </button>
    </div>

    {#if testResult}
        <div class="bg-gray-50 border rounded p-3 mb-4 text-sm font-mono">{testResult}</div>
    {/if}

    <!-- Connection list -->
    {#each connections as conn}
        <div class="bg-white rounded-lg border p-3 mb-2 flex items-center gap-3">
            <span class="flex-1 font-medium text-sm">{conn.name}</span>
            <span class="text-xs text-gray-400">{conn.url}</span>
            {#if conn.is_active}
                <span class="bg-green-100 text-green-700 text-xs px-2 py-0.5 rounded font-medium">Активно</span>
            {:else}
                <button onclick={() => activate(conn.id)}
                        class="text-xs text-blue-600 hover:underline">Активировать</button>
            {/if}
            <button onclick={() => test(conn.id)}
                    class="text-xs text-gray-500 hover:text-blue-600">Тест</button>
            <button onclick={() => remove(conn.id)}
                    class="text-xs text-red-400 hover:text-red-600">Удалить</button>
        </div>
    {/each}
</div>
