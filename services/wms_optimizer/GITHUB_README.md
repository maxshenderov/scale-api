# WMS Pallet Optimizer

**Intelligent warehouse pallet placement optimization service** powered by Google OR-Tools CP-SAT solver.

Cold-start placement for **3400+ pallets** across **1500 sections** in **~3 minutes** with **95% placement rate**.

---

## 🎯 Problem Statement

**Challenge:** Optimize placement of thousands of floor pallets into warehouse rack sections while respecting:
- Physical constraints (height, depth, weight, width with gaps)
- Narrow-aisle restrictions
- Section capacity limits
- Minimizing moves for existing pallets (when reslotting)

**Scale:** Real production incident involved **5M+ boolean variables** causing OOM/timeout on exact CP-SAT model.

---

## 🚀 Solution Architecture

### Aggregated Model (Cold Start: 0 existing pallets)

Instead of modeling `X[pallet_id, section_id]` (one boolean per pallet-section pair), we aggregate:

```python
Y[pallet_type, section_bucket] = integer count
```

**Impact on real warehouse data:**
- **Before:** 2.3M boolean variables (3406 pallets × 1490 sections, filtered by feasibility)
- **After:** 19K integer variables (41 pallet types × 1490 section buckets)
- **Reduction:** ~100× fewer variables

### Three-stage residual pipeline

After aggregate model places pallets by type:

1. **Disaggregation** — greedy best-fit assignment of concrete pallets to concrete sections within buckets
2. **Exact CP-SAT** — unfiltered `X[pallet, section]` model on leftover pallets with zero feasible pairs in stage 1
3. **Consolidation pass** — free up underfilled sections (≥33% free width) by relocating sparse occupants
4. **Virtual reslot** — joint re-optimization of leftover + already-(virtually)-placed pallets in near-miss sections

All "moves" in cold-start mode are virtual (nothing committed yet) — final plan contains only `PUT` operations.

---

## 📊 Results

**Benchmark: 3406 floor pallets → 1490 rack sections (cold start)**

| Metric | Exact model (before fix) | Aggregated + residual | Manual reference |
|--------|:------------------------:|:---------------------:|:----------------:|
| **Placed** | OOM/timeout | **3217-3239** (95%) | **3242** (95.2%) |
| **Time** | ∞ (hung) | **160-190s** | ~20 min (human) |
| **Memory** | 58GB+ (host OOM risk) | <8GB (within container limit) | N/A |

**Gap to manual:** 3-25 pallets (0.1-0.7%) — within CP-SAT parallel search variance.

---

## 🛠️ Tech Stack

- **Python 3.11+**
- **FastAPI** — REST API
- **OR-Tools 9.15** — Google Optimization Tools (CP-SAT solver)
- **Pydantic v2** — request/response validation
- **Docker** — containerized deployment

---

## 🚢 Quick Start

```bash
# Run with Docker
docker-compose up -d wms-optimizer

# Health check
curl http://localhost:8010/health
# {"status":"ok"}

# Interactive API docs
http://localhost:8010/docs
```

### API Example

```bash
curl -X POST http://localhost:8010/api/optimize \
  -H "Content-Type: application/json" \
  -d @test_request_example.json
```

**Response:**
```json
{
  "optimizationId": "test-001",
  "solverStatus": "FEASIBLE",
  "placementStatus": "COMPLETE",
  "score": 323000000,
  "executionTimeSeconds": 45.2,
  "operations": [
    {"pallet": "P001", "operation": "PUT", "newAddress": "A01-01-01"},
    {"pallet": "P002", "operation": "PUT", "newAddress": "A01-01-02"}
  ],
  "metrics": {
    "placedPallets": 3230,
    "notPlacedPallets": 176,
    "movedPallets": 0
  }
}
```

---

## 📁 Project Structure

```
wms_optimizer/
├── solver/
│   ├── cp_sat_aggregated.py   # Y[type,bucket] model + residual passes
│   ├── cp_sat_model.py        # Exact X[pallet,section] model (warm-start)
│   └── warm_start.py          # FFD heuristic
├── optimizer/
│   ├── global_optimizer.py    # Main pipeline + routing logic
│   ├── potential.py           # Section capacity/fit checks
│   └── section_optimizer.py   # Address assignment within section
├── api/
│   ├── routes.py              # Sync/async REST endpoints
│   └── schemas.py             # Pydantic models
├── tests/
│   ├── test_s7_vs_standard.py # Regression test (cold-start 3406 pallets)
│   └── example/               # Test fixtures (JSON snapshots)
└── README.md
```

---

## 🧪 Testing

```bash
# Fast smoke tests
pytest tests/test_acceptance.py -v

# Regression test (cold-start 3406 pallets, ~3min)
pytest tests/test_s7_vs_standard.py -v -s
```

**Test fixtures:**
- `tests/example/OccupancyS7.json` — 1490 sections, 0 existing pallets (cold start)
- `tests/example/FloorS7.json` — 3406 floor pallets
- `tests/example/OccupancyS6Standard.json` — manual reference layout (3242/3406 placed)

---

## 🔧 Configuration

**Automatic model selection:**
- `feasible_pairs < 300k` → exact CP-SAT model
- `feasible_pairs ≥ 300k` → aggregated model + residual passes

**Time limits:**
- Default: 120s per request
- Main aggregate model: inherits request `timeLimitSeconds`
- Residual exact pass: 20s
- Consolidation pass: greedy (no solver), ~instant
- Virtual reslot pass: 60s

**Docker limits** (prevents OOM on shared host):
```yaml
deploy:
  resources:
    limits:
      cpus: '8'
      memory: 8G
```

---

## 📈 Performance Characteristics

**Scaling:**
- Exact model: O(N_pallets × N_sections) — feasible up to ~200k boolean variables
- Aggregated model: O(N_types × N_buckets) — handles 5M+ original variable space comfortably

**Typical placement rates:**
- < 500 pallets: 98-100%
- 500-2000 pallets: 95-98%
- 3000+ pallets (cold start): 94-95%

**Unplaced reasons:**
- `HEIGHT_LIMIT` — pallet exceeds all section heights
- `NARROW_AISLE_MISMATCH` — narrow pallet, no narrow sections available
- `WEIGHT_LIMIT` — too heavy for remaining capacity
- `NO_SPACE` — all dimensionally-compatible sections full

---

## 🤝 Integration

**Designed for 1C:Enterprise ERP integration**, but API is generic:

1. Get warehouse snapshot (`occupancy` — current section state)
2. Get floor pallets to place (`newPallets`)
3. POST to `/api/optimize` or `/api/optimize/async`
4. Execute returned `operations` plan (PUT/MOVE commands)

See [API_DOCS.md](API_DOCS.md) for full endpoint documentation.

---

## 🐛 Known Limitations

- Aggregate model placement has **non-deterministic variance** (±10-20 pallets) due to CP-SAT parallel search — same input can yield slightly different output across runs
- No explicit "reserve section for product family" constraint (handled upstream in 1C business logic)
- Address assignment within section is deterministic rule, not optimized

---

## 📝 License

Proprietary — internal use only.

---

## 👤 Author

**Max Shenderov**  
Originally developed for **Лико** — label printing company (self-adhesive, shrink sleeve, FMCG packaging)

---

## 🔗 References

- [Google OR-Tools CP-SAT](https://developers.google.com/optimization/cp/cp_solver)
- [Bin Packing Problem](https://en.wikipedia.org/wiki/Bin_packing_problem)
- [First-Fit Decreasing (FFD)](https://en.wikipedia.org/wiki/First-fit-decreasing_bin_packing)
