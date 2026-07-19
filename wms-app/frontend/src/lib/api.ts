const BASE = '/api';

async function post(path: string, body: Record<string, unknown> = {}) {
    const res = await fetch(`${BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.json();
}

async function get(path: string) {
    const res = await fetch(`${BASE}${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.json();
}

export const api = {
    ping:            ()          => fetch(`${BASE}/ping`).then(r => r.json()),
    getWarehouses:   ()          => post('/warehouses'),
    getRacks:        (w: string) => post('/racks', { warehouse: w }),
    getOccupancy:    (w: string) => post('/occupancy', { warehouse: w }),
    getFloor:        (w: string) => post('/floor', { warehouse: w }),
    validate:        (data: Record<string, unknown>) => post('/validate', data),
    move:            (data: Record<string, unknown>) => post('/move', data),
    optimize:        (data: Record<string, unknown>) => post('/optimize', data),
    optimizeFloors:  (data: Record<string, unknown>) => post('/optimize/floors', data),
    executePlacements: (data: Record<string, unknown>) => post('/placements/execute', data),
    getConnections:  ()          => get('/connections'),
    getSnapshots:    ()          => post('/snapshot/list'),
    loadSnapshot:    (data: Record<string, unknown>) => post('/snapshot/load', data),
    getSnapshotData: (id?: number) => post('/snapshot/data', id ? { id } : {}),
};
