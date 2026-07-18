<script lang="ts">
  import Layout from './routes/+layout.svelte';
  import Warehouse from './routes/+page.svelte';
  import Connections from './routes/connections/+page.svelte';

  let path = $state(window.location.pathname);

  function navigate(e: MouseEvent) {
    const a = (e.target as HTMLElement)?.closest('a');
    if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
    const url = new URL(a.href);
    if (url.origin !== window.location.origin) return;
    e.preventDefault();
    history.pushState({}, '', url.pathname);
    path = url.pathname;
  }

  window.addEventListener('popstate', () => { path = window.location.pathname; });
</script>

<svelte:window onclick={navigate} />

<Layout>
  {#if path === '/connections'}
    <Connections />
  {:else}
    <Warehouse />
  {/if}
</Layout>
