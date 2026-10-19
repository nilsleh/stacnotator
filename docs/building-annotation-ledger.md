# Building Annotation Ledger (upstream `develop` port)

What was built for building-footprint labeling on top of upstream `develop`, where it
lives, and why. The plan and acceptance checks are in the
[improvement plan](building-annotation-improvement-plan.md); the how-to is in
[local labeling](local-labeling.md). The older implementation on
`feature/building-annotations` is the reference and is not merged.

## Model

The work is a **scene**: one task, outlined over the imagery. An annotator draws any number
of separate **buildings** in it, each one scored 1-5, and finishes the scene with an
**outcome**. Scenes are the unit of progress, and a whole set of them is the unit of
**hand-in**.

| Concept | Stored as | Why |
|---|---|---|
| Building campaign | `settings.building_scenes` JSONB (building, complete, empty label ids) | An explicit marker; every hook is a no-op without it |
| Scene | `AnnotationTask` (one per curated chip, `raw_source_data`: `chip_id`, `dataset_key`, `subset`, `split`, `cog_href`, `reference_buildings`) | `develop`'s tasks already give assignment, navigation and status |
| Building | Standalone `Annotation` with `origin_task_id` = scene, building label, `confidence` 1-5 | One row and geometry per building; `uq_annotation_task_user` does not apply to standalone rows, so any number per scene |
| Outcome | The user's task annotation: label "Buildings mapped" (Complete), label "No buildings" (Empty), no label (Skip); no row = Draft or To do | `develop`'s status logic stays untouched: labels count as done, label-less as skipped |
| Hand-in | `building_hand_ins` (campaign, user, time) | The one new table; withdrawn by deleting the row |
| Reference | Standalone `Annotation` with a per-campaign `source_id`, no task, no `origin_task_id` | Never part of a scene, never counted; held out of every ordinary read, admins only |

Migration: `bb1buildings` (after `at1mapaccess`).

## Backend (`backend/src/buildings/`)

- `config.py`: `BuildingScenes` and `building_scenes(campaign)`; it accepts only a real
  dict, so mocks and other campaigns read as "not a building campaign".
- `lifecycle.py`: the rules, called from `annotation/service.py`:
  - `check_new_buildings`: create and batch create, all items validated before any write.
  - `check_annotation_update`: label and score kept, geometry stays in the scene.
  - `check_scene_outcome`: Complete needs a building and every building scored; Empty
    needs none; no authoritative submits.
  - `reopen_scenes`: a building write deletes the owner's outcome (tombstoned for the
    changes poll) and withdraws their hand-in.
  - `deletion_effects` / `apply_deletion_effects`: the same for deletes.
  - `require_not_handed_in`: a hand-in locks the owner's scenes; their own creates, edits,
    deletes and outcome submits get a 409 until they withdraw it.
  - `revalidate_hand_in`: withdraws a hand-in once any of the user's scenes is unfinished,
    which only happens when someone else (an admin) changes them.
  - Progress and hand-in helpers.
- Geometry rule (`validate_building_geometry`): one valid, finite, 2D polygon with area,
  covered by the scene cell plus 1e-8° (vertices snapped onto the outline survive the
  projection round trip).
- `router.py` / `service.py`:
  - `GET .../buildings/progress`.
  - `POST|DELETE .../buildings/hand-in`.
  - `GET .../buildings/scenes/{task}/buildings` (the caller's).
  - Draft (clearing a scene's outcome) has no route of its own: the client deletes its
    task annotation through `DELETE .../annotations/{id}` (refused while handed in).
  - `GET .../buildings/references/tiles/{z}/{x}/{y}.pbf`: the references, campaign admins only.
  - `GET .../buildings/evaluation`: scoring (below), campaign admins only.
- `references.py`: references are held out of a building campaign's ordinary reads, so
  scores against them mean something:
  - the raw-SQL half is `HELD_OUT_REFERENCES_SQL` / `visible_filter_sql` in
    `annotation/tiles.py`, used by every spatial read: tiles, ids in a bbox, extent,
    density and the changes poll;
  - `visible_annotations(campaign)` filters the ORM reads: the annotation list, its facets
    and single-annotation fetches;
  - `require_reference_access` restricts `kind=references|all` exports to campaign admins.
- `evaluation.py` (pure) + `service.evaluate` (loader): the reference branch's polygon
  scoring.
  - Port: greedy one-to-one IoU matching, precision / recall / F1 / mean IoU / count
    error, overall, per split (`raw_source_data.split`) and per size class, plus the
    empty-scene false positive rate.
  - Scored: one annotator's Complete and Empty scenes. A reference belongs to the scene its
    interior lies in, since the seeder clipped each one to its chip.
  - Not carried over: temporal deduplication and pixel-mask scoring. No curated dataset is
    mask-based, and a scene's buildings are drawn once.
- `seeding.py`: the curated seeder (section below).
- Changes in `develop`'s own modules:
  - `annotation/service.py`: hooks, plus `visible_annotations` in three reads.
  - `annotation/tiles.py` / `spatial.py`: `visible_filter_sql` in place of
    `task_filter_sql`; `references=True` builds the admin reference tile.
  - `annotation/export.py`: `kind=predictions|references|all`; building campaigns default to
    predictions and keep a header when empty; other campaigns default to `all`, unchanged.
  - `campaigns/service.py` (`delete_annotation_tasks`): refuses to delete scenes that hold
    buildings unless their annotations go too, and then takes the buildings along.
  - `campaigns/duplication.py`: remaps `origin_task_id` through the task map for every
    campaign; it used to point copies at the source campaign's tasks.
  - `tilers/providers.py`: `TILER_LOCAL_COG_DIR` (local COG paths allowed past the SSRF
    guard), and a `timeout` for `register_cog_on_tiler`.

Tests: `tests/test_building_lifecycle.py`, `tests/test_building_seeding.py`,
`tests/test_building_references.py`, `tests/test_building_evaluation.py`, and
`tests/unit/test_register_cog_on_tiler.py`.

## Imagery and seeding

- `develop`'s custom maps render one band through a colormap, so they cannot show RGB
  chips. Each chip is instead registered with the tiler's `register-cog`, which creates a
  deterministic `custom-maps` item `cm-<sha1(url)[:16]>`.
- A slice is then one search: the COG's own search for a single chip, or a
  `{"collections":["custom-maps"],"ids":[...]}` mosaic for several.
- Its tile URL carries `assets=data`, `bidx` and `rescale` from the manifest's
  `tiler_params`.
- Slice tile URLs carry `tile_provider` = the tiler name and `mosaic_id` = the search id,
  as `develop`'s own registration writes them. The browser only sends the tiler cookie for
  self-hosted providers.
- The tile scope is the campaign id, so the campaign is created first and the imagery added
  after registration. Registration takes 10-50 s per COG locally, and no DB transaction is
  held open during it (the server has an idle-in-transaction timeout).
- Cells are the chips' bboxes (one per chip, as in the reference), not `develop`'s `grid`
  sampling, which would not line up with them.
- References are reprojected from their GeoJSON CRS (CRS84, UTM, Web Mercator), made 2D
  and valid, and clipped to the cell.
- `--refresh-imagery` re-registers the chips of existing campaigns, for recovery after the
  tiler database is lost.

## Frontend (`frontend/src/features/annotation/`)

- `stores/sceneDrawing.ts`:
  - Scene detection (`buildingScenesOf`, `isSceneTask`).
  - The scene's buildings.
  - The pending building awaiting its score, and the background-save outbox with
    Retry/Discard.
  - Squaring the pending building.
- `stores/sceneNav.ts`: Remaining/All stepping; it stops at the ends instead of wrapping.
  It reads outcomes off the tasks, and a draft counts as still to draw.
- `stores/buildingProgress.ts`: the server's progress and hand-in.
  `SceneProgress.tsx` reloads it after every write and drops a cached outcome the server
  has reopened, guarded against stale responses by write timestamps.
- `panels/TaskControls/`:
  - `SceneActions` (Complete / Mark empty / Skip / Draft) replaces the label, confidence
    and submit block on scenes.
  - `SceneShapes` is the buildings list.
  - `SceneProgress` holds the counts, the cycle toggle, the end card and Hand in.
- `panels/MainMap/SceneScorePrompt.tsx`: `1`-`5` score the building, `q` squares it, `Esc`
  discards it.
- `drawing.ts` / `shared/map/interactions.ts`: the Draw `canStart` gate keeps the
  interaction attached while a building waits for its score, so the finishing double-click
  never zooms.
- `keymap.ts`: scene bindings (`r`/`e`/`p`, `Enter`, `x`, `k`, `w`/`s`, `q`) in place of
  the label digits.
- `loadCampaign.ts`: a building campaign loads with every status in the filter and opens on
  the first remaining scene.
- `shared/utils/utility.ts`: `orthogonalizePolygonRing` and `squareBuilding`, ported from
  the reference, with tests.

## Verification

- Backend: the full suite (1535 tests) and ruff.
- A live API walk-through of every P3 rule: 34 checks, plus duplication and scene deletion.
- Frontend: tsc, eslint, and the annotation/shared vitest suites (1044 tests).
- Headless browser runs:
  - imagery for all five datasets;
  - draw, reload, edit, delete, Complete, Empty and export (16 checks);
  - Hand in and its withdrawal.
- `local.ps1 backup` / `verify-backup` round trip.

## Open points

- A user's scenes are those assigned to them, or every scene when none are; an
  unassigned shared pool would need a different hand-in scope.
- Agents are out of scope; `develop`'s `agents` module only answers per task.
- Evaluation is a first port. Open questions: whether it should require a hand-in, how
  multi-annotator scenes should be scored, and whether reference properties (e.g.
  SpaceNet areas) should drive the size classes.
- Under full parallel load, `vitest` shows timeout flakes in unrelated suites
  (`CreateCampaignPage`), which pass on their own.
