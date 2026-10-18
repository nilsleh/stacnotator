# Building Annotation Plan: Integrate Upstream `develop`

Updated 2026-10-16. Companion to the [implementation ledger](building-annotation-ledger.md).

**Goal:** run the local building-footprint workflow on the latest upstream `develop`
(`RAAPID-ORG/stacnotator`) instead of the stale fork `main`. Curated imagery is downloaded
once, served locally, and annotated in a local database. Nothing is pushed to any remote.
Evaluation and benchmarking come later and are out of scope.

## Decisions

- **Integrate by porting, not merging.** `develop` rebuilt the code this work changed, so
  the building features are re-implemented on top of `develop`. The old work (local branch
  `feature/building-annotations`, commit `9fcefb43`) is the reference spec.
- **Drop the 3 fork-`main` fixes.** `develop` already sorts users by display name, the
  task-counting fix touched UI files `develop` replaced, and `azure_deploy/` moved to
  `deployment/azure/`.
- **No data migration.** No user annotations exist. The 2,226 rows in the old local
  database are imported reference footprints and test data; they are re-imported from the
  curated datasets on `develop`. Old campaigns (including 35 and 41-45) are discarded.
- **Imagery is served locally** by `develop`'s optional tiler (`COMPOSE_PROFILES=tiler`)
  from `./cogs`. No hosted tiler. The old in-repo `./tiler` is not carried over.
- **Boundary policy:** polygons crossing the cell boundary are rejected, not clipped.

## Annotation workflow (confirmed)

The annotator is shown a **scene**: one outlined grid cell over the broader imagery. Inside
it they draw as many separate building polygons as needed. Together these form the
scene's collection of building annotations. Buildings can be added, edited and deleted
until the scene is finished; the scene is marked **done** only once every building in it
has been drawn. A scene with no buildings is marked **empty**; a scene can also be
**skipped** or left as a **draft** to return to later.

How this maps onto `develop`, which allows one annotation per user per task:

- Each scene is a task.
- Each building is its own polygon annotation, created with `develop`'s drawing tools and
  linked to the scene's task through `origin_task_id`. The scene's collection is all
  polygons linked to that task.
- The user's single task annotation stores only the scene outcome (done, empty, skipped,
  draft).

Polygon identity (verified in `develop`'s `Annotation` model):

- Every building is a separate annotation row with its own primary key `id` and its own
  geometry row, so individual polygons can always be told apart, edited and deleted.
- `origin_task_id` is the scene link. It is indexed and not unique, so one scene holds
  any number of polygons; the scene's collection is "all annotations with this
  `origin_task_id`".
- `uq_annotation_task_user` applies only to `annotation_task_id` (the scene outcome).
  Building polygons leave it NULL, so the one-per-task rule does not limit them.
This needs no schema change. The old approach, attaching many polygons directly to the
task, is only a fallback if P2 shows `origin_task_id` cannot enforce the scene rules in P3.
## State

- Remote `upstream` = `RAAPID-ORG/stacnotator` (fetched). `origin` = fork `nilsleh/stacnotator`.
- Fork `main` (`52c3329`) is 464 commits behind `upstream/develop`; `develop` changed 976 files.
- A trial `git merge upstream/develop` gave 47 conflicts (mostly modify/delete) and was aborted.

| Area | Old work (reference) | `upstream/develop` |
|---|---|---|
| Imagery | In-repo `./tiler`, COGs in `data/imagery`, bbox chips | Optional external tiler (built from `stacnotator-tiler`), COGs in `./cogs`, registered as custom maps (`cog_url=/data/cogs/<file>.tif`) |
| Annotation UI | Old task/annotation stores, `DrawingLayer`, `ControlsTaskMode` | Rebuilt: `features/annotation/{canvas,map,panels,stores/work.ts,drawing.ts}` |
| Polygons | Many per user per task | One annotation per user per task; open-mode polygon/line/point drawing with batch create/delete, bbox lookup and `origin_task_id` |
| Cells | `mesh_grid` sampling | `random` / `grid` sampling strategies |
| Agents | `/api/mcp`, evaluation service | `backend/src/agents` (register, next, annotate, release) |
| Migrations | `t1a2b3c4d5e6` | Single head `as1origintask` |

## Handoff rules for the implementing agent

- **Prerequisites:** Docker Desktop running; network access (the tiler image builds from
  GitHub). Curated imagery is already in `data/imagery/{sn1_rio,sn2_khartoum,sn2_shanghai,sn4_atlanta,sn7_muds}`.
- **Reference implementation:** local branch `feature/building-annotations` (commit
  `9fcefb43`). Key sources: `backend/src/annotation/{service,schemas}.py`,
  `scripts/seed_building_datasets.py`, `scripts/local.ps1`,
  `backend/tests/test_building_lifecycle.py`, `test_building_dataset_reuse.py`,
  `frontend/src/features/annotation/stores/building-lifecycle.test.ts`,
  `frontend/src/shared/utils/utility.ts` (squaring).
- **Git:** work only on local `feature/building-on-develop` (from `upstream/develop`).
  Never push or open PRs; never modify `main` or `feature/building-annotations`.
- **Order and stops:** P0 -> P0.5, then **stop for owner review** of the spike decision.
  After approval, P1 -> P5, checking each tranche's acceptance before moving on.
- **Scope:** don't port MCP/evaluation code, the in-repo tiler, or the old migration
  `t1a2b3c4d5e6` unless P0.5 chooses the fallback model.
## Integration tranches

Work on a new local branch `feature/building-on-develop` created from `upstream/develop`.

### P0 - Run `develop` locally

- [x] Create the branch. Start `develop`'s compose stack with `COMPOSE_PROFILES=tiler`,
  `TILERS` / `DEFAULT_TILER` set per `.env.example`, and optionally
  `TILER_CONTEXT=../stacnotator-tiler`. Use a fresh database.
- [x] Run `develop`'s backend tests, frontend unit tests and lint to get a clean baseline.
- [x] Create the local organization, project and user that `develop` now requires, and
  accept its terms gate once.

**Acceptance:** an empty campaign opens in the app and a sample COG from `./cogs`
renders through the local tiler. **Done** (commit `4f1c3af4`).

Notes: the stack runs from a gitignored `.env.develop` under compose project `stacdev`
(fresh volumes). Package installs go through the Microsoft feed proxy (build args), the
private tiler repo is built from `../stacnotator-tiler`, and the opt-in
`TILER_LOCAL_COG_DIR=/data/cogs` lets `cog_url` point at the tiler's disk (`develop`'s SSRF
guard otherwise rejects the documented `/data/cogs/...` paths). Baseline failures were all
environmental: tiler env vars in 5 custom-map tests, CRLF checkouts for Prettier, and
5 s vitest timeouts.

### P0.5 - Scene-drawing spike (go/no-go for the storage model)

`develop` only sets `origin_task_id` in **explore** mode: switching from a task to explore
shows a "Near task #N" breadcrumb, and polygons drawn there remember that task. The
breadcrumb clears when leaving explore. Task mode does not draw polygons inside an
outlined scene. `origin_task_id` therefore means "came from" (provenance), not "belongs to".

- [x] On a throwaway branch, let task mode in a building campaign draw standalone polygons
  with `origin_task_id` = current task, render the scene outline and only that scene's
  polygons, and reload them.
- [x] Decide: keep `origin_task_id` as the scene link (enforced for building campaigns
  only), or add a dedicated scene-link column if upstream semantics conflict.
- [x] Check that `develop`'s campaign setup can define a polygon `Building` label
  (`geometry_type: polygon`) alongside task mode.

**Acceptance:** two polygons drawn in one scene survive reload, stay separate (distinct
`id`s) and are listed only under that scene. **Done** on `spike/scene-drawing`.

**Decision (approved 2026-10-16): keep `origin_task_id` as the scene link.** No schema
change for buildings. Three rules move into P3 (see there): explore-mode shapes must not
join a scene, campaign duplication must remap the link, and scene deletion must not
orphan buildings.

Spike outcomes carried into P3/P4 (from the owner's manual test on 11 curated scenes):

- Every building needs a confidence score (1-5). Finishing a polygon opens a score prompt
  at the shape; keys 1-5 (or a click) store it, Esc discards it. The save runs in the
  background (key to next drawable: <0.1 s vs 0.1-0.7 s blocking); failed saves stay listed
  with Retry/Discard.
- Finishing a polygon must not zoom the map (the Draw interaction stays attached while
  a building waits for its score), and the draw tool stays armed from scene to scene.
- `develop` has no "done" step: each Submit is final per task, Next wraps from the last
  scene to the first, and "Submit authoritative" (a reviewer conflict override) read as
  the only action on finished scenes. See the hand-in requirements in P3/P4.
### P1 - Curated imagery

- [x] Put the five curated datasets in `./cogs` (or point `COG_DIR` at `data/imagery`).
- [x] Show the chips themselves, not substitute imagery. `develop`'s custom maps are
  single-band colormap overlays (multi-band COGs fail with `asset_as_band`), so register
  each chip on the local tiler and serve it as an imagery source whose tile URL carries the
  dataset's band/stretch (old `bidx` / `rescale`, e.g. Khartoum `1,2,3` at `0-1400`,
  Atlanta `5,3,2` at `0-1200`). Verify the browser's tiler cookie covers these tiles.

**Acceptance:** all five datasets show real image pixels, not blank or constant tiles.
**Done:** campaigns 14-18 render Rio, Khartoum (PS-RGB and the 1.2 m MS source), Shanghai,
Atlanta (`5,3,2`) and the SN7 months in the browser.

Notes: `scripts/seed_building_datasets.py` copies the chips to `cogs/curated/` (mounted at
`/data/cogs/curated`). `src/buildings/seeding.py` registers each chip with the tiler's
`register-cog` (10-50 s per COG locally, so the call takes a `timeout`), and mosaics
multi-chip slices with one search over the chips' `custom-maps` item ids. Slice tile URLs
must carry `tile_provider` = the tiler name: the browser only sends the tiler cookie for a
self-hosted provider, and without it every chip tile is refused. Some SN2 chips contain
black no-data areas; that is the source imagery.

### P2 - Building campaigns and cells

- [x] Generate grid cells using `develop`'s `grid` strategy, or add a mesh option if cell
  size/alignment cannot match. Keep `raw_source_data` markers (`chip_id`, `dataset_key`)
  so building cells are recognisable.
- [x] Port `scripts/seed_building_datasets.py` to `develop`'s organization/project/campaign
  APIs: reuse existing campaigns by default; `--reset` requires
  `--confirm-reset DELETE-ANNOTATIONS`; import reference footprints.
- [x] Confirm `origin_task_id` can hold a scene's building collection (load, count and export per scene).

**Acceptance:** seeding twice creates no duplicates; cells and references appear on the map.
**Done.**

Notes: as in the reference, each chip is one cell (its bbox), so the `grid` strategy is not
used: grid cells would not line up with the chips. Cells carry `chip_id`, `dataset_key`,
`subset`, `split`, `cog_href`, `reference_buildings` and `is_empty_reference`. The seeder
runs inside the backend container on `develop`'s services (no HTTP API covers tiler
registration), one campaign per dataset in project "Buildings (local)", all scenes assigned
to `local-user`. References are standalone annotations with a per-campaign `source_id` and
no `origin_task_id`, so they never join a scene; they are clipped to their cell. The
GeoJSON export already writes one row per building with `stacnotator_annotation_id`,
`stacnotator_origin_task_id` and `stacnotator_source_id`.

### P3 - Building lifecycle (backend)

Re-implement these behaviours from the old work on `develop`'s services:

- [x] Polygons must be valid, finite, 2D, labelled and inside their cell; batches validate
  everything before replacing saved buildings.
- [x] Scene outcomes: **Complete** requires at least one building; **Empty** is refused
  while buildings remain; **Skip** keeps drawings; **Draft** does not count as done.
  Creating, editing or deleting a building reopens a finished scene.
- [x] Building polygons must have `origin_task_id` set to a scene in the same campaign.
  `develop` makes it optional and sets it to NULL when the task is deleted, so refuse
  scene deletion while polygons exist (or delete them explicitly) to avoid orphans.
- [x] Explore-mode shapes must not join a scene: in building campaigns a shape with an
  `origin_task_id` must lie inside that scene (boundary rule), and only scene mode sends it.
- [x] Campaign duplication remaps `origin_task_id` through the task map (`develop` copies
  it unchanged, pointing duplicates at the source campaign's tasks).
- [x] Every building carries a confidence (1-5); creates without one are refused and
  updates cannot clear it. **Complete** is refused while any building is unscored.
- [x] **Hand in** (not authoritative): a per-user, per-campaign record that the assigned
  scenes are finished. Allowed only when every assigned scene is Complete or Empty;
  Skipped and Draft scenes block it. Editing a building after hand-in reopens that scene
  and withdraws the hand-in until it is handed in again (proposed default). Needs a new
  table, so this is the one schema change in the port.
- [x] Imported references get a distinct `source_id` per campaign
  (`uq_annotation_campaign_source`), keeping each reference polygon separate too.
- [x] References never count toward completion and are never mixed into user output.
- [x] Exports: one feature per building carrying its annotation `id` and scene `origin_task_id` (never merged by agreement), predictions by
  default with references only on request, and a header-only file when empty.

**Acceptance:** backend tests ported from `test_building_lifecycle.py` pass on `develop`.
**Done:** `tests/test_building_lifecycle.py` (rules, outcomes, hand-in, exports, scene
deletion) and a live API walk-through on campaign 19 (34 checks) pass; full suite green.

How it maps onto `develop` (`backend/src/buildings/`, migration `bb1buildings`):

- `settings.building_scenes` (JSONB) marks a building campaign and names its labels:
  `Building` (polygon, id 1) for shapes, and two outcome labels, `Buildings mapped` (2,
  Complete) and `No buildings` (3, Empty). The scene outcome is the user's task annotation,
  so `develop`'s status logic is untouched: Complete/Empty carry a label (done), Skip is a
  label-less submit (skipped), and Draft is no outcome while buildings exist (pending).
- Hooks in `annotation/service.py` (create, batch create, update, delete, task submit)
  call `buildings.lifecycle`; on other campaigns they do nothing. A building edit reopens
  the owner's scene by deleting their outcome (tombstoned for the changes poll).
- Explore shapes in building campaigns are held to the same rules (building label, a
  scene, a score, inside that scene), so a "Near task" shape outside the scene is refused.
- Routes: `GET .../buildings/progress`, `POST|DELETE .../buildings/hand-in`, and
  `GET .../buildings/scenes/{task}/buildings` (the caller's). Draft (clear the outcome)
  deletes the user's task annotation through the regular annotation endpoint.
- A user's scenes are those assigned to them, or every scene when none are.
- Hand-in is withdrawn whenever one of the user's scenes stops being Complete or Empty
  (a building edit, Skip, or Draft). This was the proposed default and is kept; nobody
  confirmed it in this session.
- Duplication remaps `origin_task_id` for every campaign (a copy never points at the
  source's tasks; without tasks it is cleared).
- Exports take `kind=predictions|references|all`; building campaigns default to
  predictions, other campaigns to `all` (unchanged). Scene outcome rows stay in the
  predictions export, since Empty scenes are results too.

### P4 - Building lifecycle (frontend)

- [x] Build the scene mode from the P0.5 spike: task mode shows the scene outline and draws building polygons.
- [x] Add Complete / Mark Empty / Skip / Draft actions to `develop`'s task panel.
- [x] Keep the spike's scoring flow: score prompt on finish (1-5 / Esc), background save
  with retry, no double-click zoom, draw tool armed across scenes.
- [x] Hand-in: a progress view (complete / empty / skipped / draft) and a **Hand in** button
  that is enabled once nothing blocks it. Next must not silently wrap from the last scene
  to the first; at the end it shows the summary and the hand-in.
  Keep the spike's navigation (`spike/scene-drawing` `3a17312f`, `stores/sceneNav.ts`):
  a **Remaining / All** toggle. Remaining visits untouched scenes, then skipped ones,
  never the current one; All walks in order and stops at both ends with an end card and
  "Go to the first remaining scene". The Prev/Next buttons and the W/S keys share it.
  The canvas layout stays as in `develop`: annotators resize panels with **Edit Layout**.
- [x] Hide "Submit authoritative" in building campaigns.
- [x] Drawing shows and edits only the active scene's buildings; failed saves and edits stay
  retryable; references stay hidden while drawing.
- [x] Building campaigns queue `pending` + `in_progress` scenes by default; other campaigns
  keep `develop`'s default. Navigation can reach empty and in-progress scenes.
- [x] Port the building-squaring helper into `develop`'s `utility.ts` and merge the tests.

**Acceptance:** in the browser, draw two buildings, reload, edit, delete one, complete the
scene, mark another scene empty, and export. Counts, geometry and statuses persist.
**Done:** a headless browser run on a freshly seeded campaign (16 checks: draw two buildings
with one squared, reload, re-score, delete, Complete, Mark empty, panel and server counts,
export) and a hand-in run (Hand in, then Draft withdraws it) pass; frontend type-check,
lint and the annotation/shared vitest suites (1044 tests) pass.

Notes:

- A campaign is a building campaign when `settings.building_scenes` is set; every task is
  then a scene (`stores/sceneDrawing.ts`). Buildings load from
  `GET .../buildings/scenes/{task}/buildings` (the caller's own).
- The task panel swaps label/confidence/Submit for `SceneActions`: Complete, Mark empty,
  Skip, Draft. Keys on a scene: `r`/`e`/`p` draw/edit/pan, `Enter` Complete, `x` Empty,
  `k` Skip, `w`/`s` Prev/Next, `q` squares the building being scored or edited.
- Navigation reads outcomes off the tasks (a draft counts as to draw); the panel counts
  and hand-in come from `/buildings/progress` (`stores/buildingProgress.ts`), which is
  reloaded after every write and drops a locally cached outcome the server has reopened.
- Building campaigns load with every status in the task filter and open on the first
  remaining scene, so a finished set still shows its progress and Hand in.
- Explore in a building campaign never sends `origin_task_id`; annotation tiles (and so
  references) are drawn in Explore only, never over a scene.

### P5 - Local tooling and docs

- [x] Port the backup / verify-backup actions (`scripts/local.ps1`), or document
  `develop`'s `Makefile` / `deployment/azure/utils` equivalents.
- [x] Rewrite [local labeling guide](local-labeling.md) and the [ledger](building-annotation-ledger.md)
  for the new paths, and port the live browser test to the new UI.

**Acceptance:** a backup restores into a disposable database with all campaigns intact.
**Done:** `scripts/local.ps1 backup` / `verify-backup` round trip restored 12 campaigns,
147 scenes and 2232 annotations, matching live, into a throwaway database (dropped
afterwards). The tiler database is not dumped (pgstac does not restore under `pg_dump`'s
empty `search_path`); `local.ps1 refresh-imagery` re-registers the chips instead and
reported 0 unmatched tile URLs. `scripts/building_live_check.js` is the live browser test
(16 checks, passing on fresh Rio and Shanghai campaigns).

**Milestone:** building annotation works locally on `develop`, ready for exploratory labeling. **Reached** (P0-P5 done).

## Keeping up with `develop`

- Keep `feature/building-on-develop` local; rebase on `upstream/develop` before each
  tranche and rerun its tests.
- Fork `main` and `feature/building-annotations` stay unchanged as reference only.
## Later (not in this plan)

- Evaluation and benchmarking of label quality.
- Agent/MCP work: build on `develop`'s `agents` module rather than porting the old
  `mcp_server.py` / `evaluation_service.py`. Agents today only answer per task; drawing
  buildings in a scene (polygons + scene outcome + hand-in) is out of scope for this port.