# Agentic labelling

A model can label the tasks of a campaign, alone or as a team of subagents. It works
through the `stacnotator` MCP server in the SDK: it looks at packed images of the imagery
around each task, asks for more detail when a point is unclear, and submits labels like any
other annotator.

## Setup

Install the plugin, which brings the labelling skill and the MCP server. You need
[uv](https://docs.astral.sh/uv/); there is nothing to clone or run yourself.

Claude Code:

```
/plugin marketplace add RAAPID-ORG/stacnotator
/plugin install stacnotator@stacnotator
```

Codex:

```bash
codex plugin marketplace add RAAPID-ORG/stacnotator
codex plugin add stacnotator@stacnotator
```

The agent always asks which STACNotator to use and needs its full URL. Then ask, e.g. "label 20 points of the Ukraine winter crop campaign on
https://your-stacnotator.example.org with 4 agents". The agent signs you in through a
browser tab the first time and asks once before downloading Chromium (about 150 MB), which
draws the images. The first start takes a minute while uv installs the SDK.

Other MCP clients: run
`uvx --from "stacnotator-sdk[agent] @ git+https://github.com/RAAPID-ORG/stacnotator#subdirectory=sdk" stacnotator-mcp`
over stdio and give the model
[`sdk/skills/stacnotator-labelling/SKILL.md`](../sdk/skills/stacnotator-labelling/SKILL.md).

On a bare Linux machine Chromium may also need system libraries:
`uvx playwright install-deps chromium` (needs sudo).

A campaign with point tasks needs a sample extent set: it is the red box drawn around each
point so the model knows what it is labelling.

## Running a labelling run

Ask in plain words, e.g. "label 20 points of the Ukraine winter crop campaign with 4 agents".
The skill asks for whatever is missing (campaign, number of points, number of agents), then:

1. registers one agent per worker, with the points split evenly and take-over switched on,
2. launches one subagent per agent, each looping next task, extra views when needed, label or skip,
3. reports a table of task, label, confidence and evidence.

Registering also opens a window on your screen with each agent's latest views, which
explains what you are looking at and where to follow progress; closing it does not stop the
agents, and `watch_agents` reopens it. If you stop a run early, ask it to free the agents'
tasks, or use the Agents page.

## Evaluation runs: StacNotator tools only

A building campaign seeded from a curated dataset holds that dataset's footprints as hidden
references (see [local-labeling.md](local-labeling.md)). For scored runs, separate trusted
owner setup from execution: register the agent as the owner, then create an execution
credential with `POST /api/agents/{agent_id}/credentials` and
`{"expires_in":3600}`. The response contains `token`, `credential_id` and `expires_at`.
The token is returned once; only its hash is stored. Lifetimes range from 60 seconds to
24 hours. Revoke it with
`DELETE /api/agents/{agent_id}/credentials/{credential_id}`.

Supply `STACNOTATOR_EXECUTION_TOKEN` and `STACNOTATOR_URL` to the MCP process through its
environment, not a prompt or a committed config. For separate API hosting also set
`STACNOTATOR_API_URL`. Resume the pre-registered agent with `agent_progress` and
`open_scene`; execution mode cannot register agents, sign in as the owner or switch
deployments. Expired/revoked credentials fail instead of refreshing with a saved owner
login. The renderer receives the same execution token.

Execution credentials allow only their agent/campaign context, imagery, own assigned
scene reads, skips and lifecycle writes. References, evaluation, exports, other agents,
administration and task reassignment are denied at the API. API revocation is immediate;
an already-issued campaign-only imagery cookie may remain valid for up to 60 seconds.
**Local authentication is not a security boundary:** ordinary local requests deliberately
authenticate as the built-in admin. Use a real authentication provider for isolation
against arbitrary callers.

The datasets remain public, so a model with shell, file or web access could find answers
outside STACNotator. Restrict the runner to STACNotator tools as defense in depth:

```bash
mkdir -p ~/stacnotator-eval && cd ~/stacnotator-eval   # outside the repo and data/
cat > mcp.json <<'EOF'
{"mcpServers": {"stacnotator": {"command": "uvx", "args": ["--from",
  "stacnotator-sdk[agent] @ git+https://github.com/RAAPID-ORG/stacnotator#subdirectory=sdk",
  "stacnotator-mcp"]}}}
EOF
claude --tools "" --strict-mcp-config --mcp-config ./mcp.json
```

`--tools ""` removes every built-in tool (shell, files, web search and fetch, subagents);
`--strict-mcp-config` loads only the server in `mcp.json`. Without subagents a session runs
one agent: for several, start one session each. Other clients need the equivalent - no
built-in tools, this one MCP server. Scores come from the campaign admin's
`GET /campaigns/{id}/buildings/evaluation?user_id=<agent>`, never from the agent's session.

## Building campaigns

In a building campaign every scene is labelled by everyone assigned to it and each labeller
is scored on their own, so an agent does not take scenes from the open pool: registering
with `split` (`val` or `test`, matched against the scene's `raw_source_data.split`) gives it
its own assignment for `task_count` scenes of that split. Only campaign admins can do this;
other members hand over their own assigned scenes as usual.

The agent traces buildings in returned-image pixels, not native TIFF pixels. Every view's
metadata carries each cell's rectangle (`rect: [x, y, w, h]`), projection and affine
`pixel_to_map: [a,b,c,d,e,f]`: image `(x,y)` maps to `(a*x+b*y+c,d*x+e*y+f)` in
EPSG:3857. The SDK converts that to WGS84 through
[`_pixels.py`](../sdk/src/stacnotator/_pixels.py). The buildings are saved through the
same bulk annotation code as the annotation page, tied to the scene by `origin_task_id`,
and drawn back in cyan in the agent's later views. `finish_scene` records the complete or
empty outcome and `hand_in` locks the agent's work, exactly as for a person; a scene
holding buildings cannot be marked empty, and hand-in refuses while a scene is open or
skipped.

### Render references and editing

Each returned image has an immutable `render_id`, agent/task binding, `image_sha256`,
dimensions, per-cell rectangles and rendering configuration. Archive the MCP Image
bytes alongside their JSON metadata to preserve the returned image. References live only
in the MCP process, not the backend or database. The SDK retains at most 256 references;
restart or eviction requires fresh views. Unknown, cross-agent, cross-scene or wrong-image
references fail, never fall back to the latest image.

Pass `render_id` and the image's 1-based `image` number to `draw_buildings`,
`edit_building` and `finish_scene`. `edit_building` replaces an owned polygon while
preserving its ID, accepts interior `holes`, validates confidence/containment and reopens
finished work unless handed in. Deletion remains explicit. Pixel rings must stay in one
map cell, outside caption space. The backend accepts WGS84 geometry, not render references.

Building views fit the entire scene by default, with captions above the useful map area.
Explicit zoom overrides are preserved; `fit_scene` requests scene fitting. Per-chip
campaigns pin each task to its imagery slice and reject a different slice.
Per-cell status is `ready`, `partial` or `failed`, with tile errors, pending tiles,
timeout and in-scene missing-pixel coverage. Transparency outside the scene is not a
missing-data error. Failed/partial renders are not retained in the page cache.
Pixel tracing rejects failed imagery and MCP finishing requires a ready imagery render.
The API enforces scene ownership and lifecycle rules, not whether a client rendered or
looked at an image. Restricted building clients submit completion through the scene outcome
endpoint, not generic label submission.

### Recovery and retries

Writes are not deduplicated and ambiguous responses must not be retried blindly. Registration
and assignment commit together, and a batch of buildings is saved atomically. A timeout
can still occur after a successful commit. After uncertain registration, the owner must
inspect `list_agents` and recover the existing agent rather than automatically create
another. Keep its `agent_id` outside the MCP process.

After an uncertain building write, read
`GET /agents/{id}/buildings/progress` (`agent_progress`) and
`GET /agents/{id}/tasks/{task}/scene` (`open_scene`) to reconcile current buildings,
todo/draft/skipped/complete/empty states and hand-in before another mutation. These reads
show current state, not proof that a particular request ran; if the result remains ambiguous,
stop for owner reconciliation instead of resending a creation request. `open_scene` restores the MCP
current scene after restart or revisits skipped/finished work; it does not change an
outcome or unlock handed-in work. Set image limits again and request fresh views after
restarting. Normal resume never releases assignments. An empty `next_task` queue is not
proof of completion: skipped scenes still block hand-in.

## How it works

```mermaid
sequenceDiagram
    participant M as Model / subagent
    participant S as MCP server (SDK)
    participant P as Headless page per agent
    participant B as Backend
    M->>S: next_task(agent_id)
    S->>B: POST /agents/{id}/next
    B-->>S: current task + next two
    S->>P: render(task, default views)
    P->>B: campaign, tile tokens, time series
    P-->>S: JPEGs + per-cell metadata
    S->>P: preload(next two tasks)
    S-->>M: task JSON + images
    M->>S: submit_label(...)
    S->>B: POST /agents/{id}/tasks/{task}/annotate
```

- **Who can run agents.** Only project members. Campaign admins hand agents tasks from the
  open pool; other members only the tasks assigned to themselves, and freeing an agent's
  unfinished tasks gives them back to that member. At most 20 agents per person in a
  campaign.
- **Agents are annotators.** Registering creates a user owned by the signed-in account, adds
  it to the project and assigns it tasks with the normal assignment code. Labels, skips,
  statistics and review rows are ordinary annotations. `data.labelling_agents` only records
  the owner, campaign, description and the take-over setting.
- **Agents are not members.** They hold a project membership row so assignments work, but a
  run is temporary, so they are left out of the project members list, the users an admin can
  add, and the campaign's `users.csv`. Their work still shows up wherever annotations are
  attributed: statistics, the review page and the annotation exports.
- **Nothing is rendered on the server.** The MCP server opens `/agent-render?campaign=<id>`
  of the app in its own browser context per agent (so agents do not share tile connections)
  and calls `window.stacnotatorAgent` on it. The page draws with the same catalog, tile URLs,
  Planet scene search and time series code as the annotation page and returns the images.
  It signs in with the SDK login, which the MCP server hands to the page on request.
- **Campaign context and default views come from the page**, built from the campaign it
  loaded: sources with collections and slices, basemaps, time series, labels, form fields
  and the guide.
- **Views.** A view is a grid of cells, each a slice (with a visualization), a basemap or a
  time series chart (optionally cloud-free and smoothed), at a zoom and cell size the model
  picks. Each cell carries a short readable label (`#3 May 2025, z17`); the metadata adds
  the exact dates, zoom, meters per pixel, the time series values and, for point tasks, the
  pixels inside the sample extent as numbers (mean colour, a green index, the share of
  bright and of missing pixels). Areas without tiles are hatched and flagged, and the red box
  is drawn outside the extent so nothing inside it is covered.
- **Image limits.** Models downscale large images before they see them, which silently
  loses detail. The model states the largest image it takes in unchanged when it registers
  an agent, and every view is fitted to that by shrinking its cells.
- **Step by step per point.** `next_task` returns a light first look (a few dates spread over
  the season, zoomed on the point, the time series and a basemap pair); the agent then calls
  `get_views` on the same point as often as it needs for other dates, zooms, cell sizes,
  visualizations or basemaps. Drawn views are cached, so asking again costs nothing.
- **Preloading.** Every `next_task` draws the agent's default views for its next two tasks in
  the background, so a steady loop gets its images without waiting. Default views live in
  the MCP server's memory and reset when it restarts.
- **Take-over.** An agent with take-over on that runs out of tasks takes the last queued task
  of the owner's agent on the campaign with the most work left.

## Agents page

`/projects/<p>/campaigns/<c>/agents` lists your agents on a campaign with done and remaining
counts and when each last submitted a label, so a run that died is easy to spot. From there you can switch take-over per agent and free the unfinished tasks of one
agent or of all of them; labelled and skipped tasks stay. The page also explains the feature
and how to install the plugin, and the campaign overview links to it under the task sets.

## API

| route | |
| --- | --- |
| `POST /campaigns/{id}/agents` | register an agent and assign it `task_count` tasks |
| `GET /campaigns/{id}/agents` | total and open tasks, and your agents with their progress |
| `POST /campaigns/{id}/agents/release-tasks?agent_id=` | free unfinished tasks of one or all of your agents |
| `GET /agents/{agent_id}` | one agent |
| `PATCH /agents/{agent_id}` | switch take-over |
| `POST /agents/{agent_id}/next` | the current task and the next two, taking over work when empty |
| `POST /agents/{agent_id}/tasks/{task_id}/annotate` | label or skip |
| `POST /agents/{agent_id}/tasks/{task_id}/buildings` | add buildings to a scene (WKT, confidence) |
| `POST /agents/{agent_id}/tasks/{task_id}/buildings/delete` | delete some of the agent's buildings in a scene |
| `POST /agents/{agent_id}/tasks/{task_id}/outcome` | mark a scene complete or empty |
| `POST /agents/{agent_id}/buildings/hand-in` | hand in the agent's scenes |

## Limits

- Everything runs on the machine of whoever runs the MCP server: each agent's page is a
  Chromium renderer drawing several maps at once, so many parallel agents are bound by that
  machine's memory and CPU.
- Registrations go one at a time: task assignment locks the campaign's open pool, so two at
  once would leave the second agent without tasks.
