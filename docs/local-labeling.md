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
- copies the chips to `cogs/curated/`, which the tiler sees as `/data/cogs/curated/`;
- runs `src/buildings/seeding.py` in the backend container.

Each campaign gets:

- one scene per chip;
- the chips as imagery: one time slice per off-nadir angle or month where the dataset has
  them, otherwise one mosaic of all chips;
- a second source for Khartoum's multispectral chips;
- the reference footprints, imported as standalone annotations with a `source_id`.

Seeding again reuses campaigns by name and changes nothing. Recreating them deletes their
annotations, so it needs an explicit confirmation:

```powershell
python scripts/seed_building_datasets.py --only sn1_rio --reset --confirm-reset DELETE-ANNOTATIONS
```

The seeder expects the project "Buildings (local)" and the user `local-user` to exist
(`--project`, `--user`). All scenes are assigned to that user.

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
Complete or Empty, **Hand in** records that your set is finished. Any later change to one of
your scenes withdraws the hand-in until you hand in again.

Explore mode shows every building and the references, but adds no buildings to a scene.
References are never shown while you draw.

The canvas layout is `develop`'s; resize panels with **Edit Layout**.

## 4. Exporting

**Export** in the toolbar, or the API:

- `GET /api/campaigns/{id}/export-annotations-geojson` and `.../export-annotations` (CSV).
- Each building is one feature, carrying `stacnotator_annotation_id` and its scene in
  `stacnotator_origin_task_id`. Scene outcomes are rows labelled "Buildings mapped" /
  "No buildings", with the scene geometry.
- The default `kind=predictions` leaves references out. Use `kind=references` for the
  imported footprints, or `kind=all` for both.
- An empty building export still has its header.

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
