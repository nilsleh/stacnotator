# Local Building Labeling

How to run building-footprint labeling on your machine, on upstream `develop`
(branch `feature/building-on-develop`). Nothing here talks to a remote service, apart from
package feeds when images are first built.

## 1. Prerequisites

- Docker Desktop with Linux containers.
- The tiler source checked out next to this repo: `../stacnotator-tiler`. Its GitHub repo is
  private, so the stack builds it from the local checkout.
- A gitignored `.env.develop` at the repo root. Start from `.env.example` and set:

  | Setting | Value |
  |---|---|
  | `COMPOSE_PROJECT_NAME` | `stacdev` |
  | `COMPOSE_PROFILES` | `tiler` |
  | `TILER_PORT` | `8083` |
  | `TILERS` | `{"hosted":{"url":"http://localhost:8083","internal_url":"http://tiler:8000","allows_ingest":true}}` |
  | `DEFAULT_TILER` | `hosted` |
  | `TILER_LOCAL_COG_DIR` | `/data/cogs` (lets `cog_url` point at the tiler's disk) |
  | `COG_DIR` | `./cogs` |
  | `TILER_CONTEXT` | `../stacnotator-tiler` |

  Local auth is used: any bearer token is accepted, as `local-user`.
- Behind a package-feed proxy, build the Python base image once (see
  `deployment/local/pip-proxy-base.Dockerfile`) and set the proxy build args in `.env.develop`.
- Curated imagery in `data/imagery/` (`sn1_rio`, `sn2_khartoum`, `sn2_shanghai`, `sn4_atlanta`,
  `sn7_muds`) with `data/imagery/curated_subsets_manifest.json`.

## 2. Start, stop, seed

```powershell
.\scripts\local.ps1 start      # compose up, migrations, waits for http://localhost:5173
.\scripts\local.ps1 seed       # building campaigns, one per curated dataset
.\scripts\local.ps1 status     # or: stop, logs
```

`seed` runs `python scripts/seed_building_datasets.py`, which:

- checks the curated files;
- copies the chips to `cogs/curated/`, which the tiler sees as `/data/cogs/curated/`, each
  under a content-hash name, so the public dataset's file names never reach the tiler or
  the app;
- runs `src/buildings/seeding.py` in the backend container.

Each campaign gets:

- one scene per chip, whose task data holds only its split: no chip id, file name,
  reference count or empty flag, since every member can export it;
- the chips as imagery: one time slice per off-nadir angle or month where the dataset has
  them, otherwise one mosaic of all chips;
- a second source for Khartoum's multispectral chips;
- no basemap, since street maps draw building footprints; for the same reason the minimap
  of a building campaign stops at zoom 12;
- the reference footprints, imported as standalone annotations with a `source_id`.

Campaign names are visible to annotators and agents, so they name the place and sensor,
never what the scenes hold (no "+ empty tile").

Seeding again reuses campaigns by name and changes nothing. Recreating them deletes their
annotations, so it needs an explicit confirmation:

```powershell
python scripts/seed_building_datasets.py --only sn1_rio --reset --confirm-reset DELETE-ANNOTATIONS
```

The seeder expects the project "Buildings (local)" and the user `local-user` to exist
(`--project`, `--user`). All scenes are assigned to that user.

### RAMP campaigns

With the RAMP dataset downloaded to `data/datasets/ramp/` (`scripts/download_eval_datasets.py
ramp`), build a manifest and seed it (`pip install rasterio` once on the host):

```powershell
python scripts/build_ramp_manifest.py                       # cities etc. from scripts/ramp_campaigns.json
python scripts/build_ramp_manifest.py --cities accra_ghana nairobi_kenya --scenes-per-city 50
python scripts/seed_building_datasets.py --manifest data/datasets/ramp/stacnotator_manifest.json
```

One campaign per city ("RAMP - Accra, Ghana"), one scene per RAMP chip (256 x 256 px at
about 30 cm). `scripts/ramp_campaigns.json` sets the cities, `scenes_per_city`, the share
of empty chips (`empty_fraction`), the split shares and the seed; the rest of the sample is
spread evenly over sparse, medium and dense chips, and the same config always picks the
same chips. 16-bit cities (Shanghai) get one contrast stretch per band.

## 3. Labeling a scene

Open a campaign's **Annotate** page. A building campaign opens in Tasks mode on the first
scene still to draw.

1. Pick **Draw Building** (`r`), click the corners of a building, and double-click to finish.
2. A prompt asks for a confidence: press `1`-`5`. `q` squares the corners first; `Esc`
   discards the building. The save runs in the background, so you can start the next
   building at once. A failed save stays in the list with **Retry** / **Discard**.
3. The **Buildings** list shows the scene's buildings. Re-score or delete them there, or edit
   the shape with **Edit** (`e`): `q` squares it and `Enter` saves it.
4. Finish the scene:

   | Action | Key | Effect |
   |---|---|---|
   | **Complete** | `Enter` | Every building is drawn; needs at least one, all scored |
   | **Mark empty** | `x` | The scene has no buildings; refused while any remain |
   | **Skip** | `k` | Come back later; the buildings are kept |
   | **Draft** | | Take the outcome off a scene; its buildings stay |

   Adding, editing or deleting a building reopens a finished scene.
5. **Next** / **Prev** (`s` / `w`) follow the **Remaining / All** toggle:
   - **Remaining** visits scenes still to draw, then skipped ones.
   - **All** walks the list in order and stops at both ends.

The **Scenes** panel counts Complete, Empty, Skipped, Draft and To do. Once every scene is
Complete or Empty, **Hand in** records that your set is finished and locks your scenes:
drawing, scoring, deleting, Complete, Empty, Skip and Draft are refused until you press
**Withdraw hand-in**. Change what you need, then hand in again.

Explore mode shows every building, but adds no buildings to a scene.

The reference footprints are held out: you are scored against them, so no map, list or
poll shows them. Campaign admins can tick **Show reference footprints (admins)** under
Display in Explore to draw them as a dashed, read-only layer. For agent runs that are
scored, see [Evaluation runs](agentic-labelling.md#evaluation-runs-stacnotator-tools-only).

The canvas layout is `develop`'s; resize panels with **Edit Layout**.

## 4. Exporting

**Export** in the toolbar, or the API:

- `GET /api/campaigns/{id}/export-annotations-geojson` and `.../export-annotations` (CSV).
- Each building is one feature, carrying `stacnotator_annotation_id` and its scene in
  `stacnotator_origin_task_id`. Scene outcomes are rows labelled "Buildings mapped" /
  "No buildings", with the scene geometry.
- The default `kind=predictions` leaves references out. `kind=references` (the imported
  footprints) and `kind=all` (both) are for campaign admins only.
- An empty building export still has its header.
- CSV and GeoJSON retain email attribution and also include
  `stacnotator_created_by_user_id`. Filter to an agent with `user_id=<agent UUID>`;
  merged rows list contributing UUIDs in sorted, comma-separated order. Building IDs,
  `stacnotator_origin_task_id` and confidence remain in the export.

## 4b. Evaluation

`GET /api/campaigns/{id}/buildings/evaluation` (campaign admins) scores an annotator's
**Complete** and **Empty** scenes against the references:

- `user_id` defaults to you; `split=few_shot|val|test` restricts to one split; and
  `iou_threshold` (default 0.5) is the IoU a building needs to match a reference.
- Matching is greedy one-to-one, best IoU first. A building with no match is a false
  positive, a reference with no match a false negative.
- Returns precision, recall, F1, mean IoU and the count error, overall, per split and per
  size class (<25 m?, 25-100 m?, >100 m?). It also gives the empty-scene false positive
  rate (scenes without references in which something was drawn) and per-scene details
  with matched pairs and the unmatched ids.
- A scene without references that was marked Empty scores a perfect 1.0. Draft, Skipped
  and To do scenes are not scored.
- Always specify `user_id=<agent UUID>` when scoring an agent. Results include
  `assigned_count`, `finished_count`, `evaluated_count`, and `coverage` counts for every
  scene state (restricted to `split` when supplied). `status=no_evidence` means no scenes
  were evaluated, not successful completion. `empty_scene_fpr_has_evidence=false` means
  there was no empty-reference denominator; the legacy numeric FPR is still present.
- Scoring version `greedy-iou-provenance-v2` retains the matching algorithm and threshold,
  but uses explicit reference-to-scene links instead of a spatial interior-point lookup.
  Imports through the building seeder set `reference_task_id`; campaign duplication
  remaps it, the pinned slice and trusted scene-source metadata to the copy.
- Migration `bf1sceneprovenance` backfills a legacy reference only when exactly one scene
  in the entire campaign covers it. Overlapping or unmatched references remain unresolved.
  Evaluation then returns HTTP 409 with example reference IDs rather than choosing an
  arbitrary scene. A trusted administrator must resolve these links from original import
  provenance, checking campaign/scene identity. Generic CSV/GeoJSON reference imports
  without scene provenance also require this reconciliation. Do not reset or reseed
  annotated campaigns to resolve it.

### Imagery and geometry preflight

New RAMP manifests use mask-aware stretch and validate RGB band order, dynamic range,
CRS and affine transforms. They preserve dimensions, nodata, color interpretation and
the densified transformed chip footprint. `scene_imagery=per_chip` prevents a neighboring
acquisition from being substituted by a mosaic. Source chip identity and raster metadata
live in the trusted scene-source table, not ordinary agent task output.

`settings.building_scenes.geometry_policy` defaults to `legacy-v1`; new RAMP manifests
opt into `polygon-v1`, which explicitly rejects multipart reference buildings.
Polygon holes are supported. References are clipped at the scene boundary; predictions
must stay inside it with the existing numerical tolerance, including boundary-touching
polygons. Predictions are not silently clipped.

`footprint_semantics` is `source-native` by default, or explicitly `roof` / `ground`.
New RAMP manifests explicitly select `roof`: the authoritative
[RAMP training-data documentation](https://rampml.global/training-data-documentation/)
instructs labelers to outline rooftops and distinguishes RAMP's rooftop target from
SpaceNet's ground footprints, whose off-nadir displacement must be corrected for RAMP.
This does not relabel existing campaigns or establish the semantics of other datasets.
Source-native remains unresolved, not proof of either interpretation. Verify partial edge
buildings, RGB, embedded nodata and alignment on a real chip before expanded evaluation.
The seeder also accepts an explicit finite `nodata` override through its existing tiler
parameter path when required by a verified source.

## 5. Backup and restore

```powershell
.\scripts\local.ps1 backup                                    # data\backups\stacnotator-<time>.dump
.\scripts\local.ps1 verify-backup -BackupPath <file>.dump     # restore into a throwaway DB, count rows
```

Run `verify-backup` before relying on a backup.

- The backup holds the app database: campaigns, scenes, buildings, outcomes and hand-ins.
- Imagery files and `.env.develop` are not included; keep them separately.
- The tiler database (registered chip searches) is not backed up: pgstac dumps do not
  restore cleanly. After restoring onto a fresh stack, or if the tiler database is lost, run:

  ```powershell
  .\scripts\local.ps1 refresh-imagery
  ```

  This re-registers every campaign's chips. Registration is deterministic, so the stored tile
  URLs work again; any that would not are reported as `unmatched`.

To restore for real, `pg_restore` the dump into the `db` service's database while the
backend is stopped.

## 6. Live check

`node scripts/building_live_check.js <projectId> <campaignId>` drives the real stack in
headless Chromium. It draws, reloads, edits, deletes, completes, marks empty, and checks the
panel, the server state and the export. Run it on a freshly seeded campaign with untouched
scenes.

## 7. Troubleshooting

- **Chips are blank or tiles return 401**: the slice tile URLs need `tile_provider` set to the
  tiler name; the seeder stamps it. After a tiler reset, run `refresh-imagery`.
- **"Not a building campaign"**: the campaign lacks `settings.building_scenes`. Only seeded
  campaigns, or ones configured like them, have scene mode.
- **A building is refused**: buildings must be single 2D polygons inside the scene outline,
  with a 1-5 score. The API's error message names the rule.
## Agent recovery

Agent clients should persist `operation_id` UUIDs before registration and building
mutations, and retry ambiguous responses with the identical request. Registration and
assignment now commit atomically. Use the agent progress and individual scene reads to
resume without releasing assignments; a skipped scene is unfinished even with an empty
queue. See [agent recovery and retries](agentic-labelling.md#recovery-and-retries).
