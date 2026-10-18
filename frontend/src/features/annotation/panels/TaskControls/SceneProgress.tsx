import { useEffect, useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import type { AnnotationTaskOut, BuildingProgressOut } from '~/api/client';
import { chipCls as chip } from '~/shared/ui/pill';
import { campaignState, useCampaign, useCampaignStore } from '../../stores/campaign';
import { useBuildingProgressStore, writtenSince } from '../../stores/buildingProgress';
import { useSceneTaskId } from '../../stores/sceneDrawing';
import {
  firstRemainingScene,
  myAnnotation,
  reviewAllScenes,
  sceneProgress,
  sceneScope,
  useSceneNavStore,
  type SceneCycle,
} from '../../stores/sceneNav';
import { useTasksStore } from '../../stores/tasks';
import { useWorkStore } from '../../stores/work';

const actionButton =
  'inline-flex items-center justify-center h-7 px-2.5 text-[11px] font-medium whitespace-nowrap rounded-md border transition-colors disabled:opacity-40 disabled:cursor-not-allowed';

/** The user's outcomes across the task list, as a string that only changes
 *  when one of them does. */
const outcomeKey = (tasks: AnnotationTaskOut[], userId: string | null) =>
  tasks
    .map((t) => {
      const mine = myAnnotation(t, userId);
      return mine ? `${t.id}:${mine.label_id ?? '-'}` : '';
    })
    .join(',');

/**
 * A building edit reopens its scene on the server, which the task list cannot
 * know: drop the stale outcome of any scene the server now counts as unfinished.
 */
function dropReopenedOutcomes(progress: BuildingProgressOut, requestedAt: number): void {
  const { currentUserId } = useCampaignStore.getState();
  const tasks = useTasksStore.getState();
  const open = new Set(
    progress.scenes.filter((s) => s.state === 'draft' || s.state === 'todo').map((s) => s.task_id)
  );
  const stale = tasks.allTasks
    .filter(
      (t) =>
        open.has(t.id) &&
        !writtenSince(t.id, requestedAt) &&
        myAnnotation(t, currentUserId) !== undefined
    )
    .map((t) => ({
      ...t,
      annotations: t.annotations.filter((a) => a.created_by_user_id !== currentUserId),
    }));
  if (stale.length) tasks.mergeTasks(stale, campaignState().catalog);
}

/** Keeps the server's view of the scenes current while a scene is open. */
function useBuildingProgressSync(campaignId: number): BuildingProgressOut | null {
  const revision = useWorkStore((s) => s.annotationRevision);
  const currentUserId = useCampaignStore((s) => s.currentUserId);
  const outcomes = useTasksStore((s) => outcomeKey(s.allTasks, currentUserId));
  const progress = useBuildingProgressStore((s) =>
    s.campaignId === campaignId ? s.progress : null
  );
  useEffect(() => {
    void useBuildingProgressStore.getState().load(campaignId);
  }, [campaignId, revision, outcomes]);
  useEffect(() => {
    if (progress) dropReopenedOutcomes(progress, useBuildingProgressStore.getState().requestedAt);
  }, [progress]);
  return progress;
}

/** Where the annotator stands across their scenes, how Next cycles, and the
 *  hand-in once every scene is Complete or Empty. */
export function SceneProgressPanel() {
  const sceneTaskId = useSceneTaskId();
  if (sceneTaskId === null) return null;
  return <SceneProgressBody sceneTaskId={sceneTaskId} />;
}

function SceneProgressBody({ sceneTaskId }: { sceneTaskId: number }) {
  const campaign = useCampaign();
  const currentUserId = useCampaignStore((s) => s.currentUserId);
  const { allTasks, filter } = useTasksStore(
    useShallow((s) => ({ allTasks: s.allTasks, filter: s.filter }))
  );
  const { cycle, endAt, setCycle } = useSceneNavStore();
  const busy = useBuildingProgressStore((s) => s.busy);
  const progress = useBuildingProgressSync(campaign.id);
  const scope = useMemo(
    () => sceneScope(allTasks, filter, currentUserId),
    [allTasks, filter, currentUserId]
  );

  // Navigation reads outcomes off the tasks: done (Complete or Empty),
  // skipped, or still to draw (a draft counts as still to draw).
  const nav = sceneProgress(scope, currentUserId);
  const counts = progress?.counts;
  const complete = counts?.complete ?? nav.done;
  const empty = counts?.empty ?? 0;
  const skipped = counts?.skipped ?? nav.skipped;
  const draft = counts?.draft ?? 0;
  const todo = counts?.todo ?? nav.todo;
  const total = progress?.scenes.length ?? nav.total;
  const finished = complete + empty;
  const remaining = nav.todo + nav.skipped;
  const allDone = total > 0 && remaining === 0;
  const atEnd = endAt === sceneTaskId;
  const percent = total ? Math.round((finished / total) * 100) : 0;
  const handedInAt = progress?.hand_in.handed_in_at ?? null;
  const canHandIn = progress?.hand_in.can_hand_in ?? false;

  const cycles: { id: SceneCycle; label: string }[] = [
    { id: 'remaining', label: `Remaining (${remaining})` },
    { id: 'all', label: `All (${nav.total})` },
  ];

  const handIn = () => void useBuildingProgressStore.getState().handIn(campaign.id);
  const withdraw = () => void useBuildingProgressStore.getState().withdraw(campaign.id);

  return (
    <div
      data-testid="scene-progress"
      data-complete={complete}
      data-empty={empty}
      data-skipped={skipped}
      data-draft={draft}
      data-todo={todo}
      data-handed-in={handedInAt ? 'true' : 'false'}
      className="flex flex-col gap-1.5 p-3 border-b border-neutral-100 w-full"
    >
      <div className="flex flex-col gap-0.5">
        <span className="font-semibold text-neutral-700 text-xs tracking-wide">Scenes</span>
        <span className="text-[11px] text-neutral-500 tabular-nums">
          <span className="font-semibold text-neutral-900">{complete}</span> complete ·{' '}
          <span className="font-semibold text-neutral-900">{empty}</span> empty ·{' '}
          <span className={skipped ? 'text-amber-700 font-semibold' : ''}>{skipped}</span> skipped ·{' '}
          <span className={draft ? 'text-amber-700 font-semibold' : ''}>{draft}</span> draft ·{' '}
          {todo} to do · {total} total
        </span>
      </div>
      <div className="h-1.5 w-full bg-neutral-200 rounded-full overflow-hidden">
        <div className="h-full bg-brand-600" style={{ width: `${percent}%` }} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5" title="What Next and Prev step through">
        <span className="text-[11px] text-neutral-500">Next goes through</span>
        {cycles.map((c) => (
          <button
            key={c.id}
            type="button"
            data-testid={`scene-cycle-${c.id}`}
            aria-pressed={cycle === c.id}
            className={chip(cycle === c.id)}
            onClick={() => setCycle(c.id)}
          >
            {c.label}
          </button>
        ))}
      </div>

      {handedInAt ? (
        <div
          data-testid="scene-handed-in"
          className="flex flex-col gap-1.5 mt-1 p-2 rounded-md bg-emerald-50 border border-emerald-200"
        >
          <span className="text-xs font-semibold text-emerald-800">
            Handed in {new Date(handedInAt).toLocaleString()}.
          </span>
          <span className="text-[11px] text-emerald-800">
            Changing a scene withdraws the hand-in until you hand in again.
          </span>
          <button
            type="button"
            data-testid="scene-withdraw"
            disabled={busy}
            className={`${actionButton} self-start bg-white text-neutral-700 border-neutral-300 hover:bg-neutral-50`}
            onClick={withdraw}
          >
            Withdraw hand-in
          </button>
        </div>
      ) : allDone ? (
        <div
          data-testid="scene-all-done"
          className="flex flex-col gap-1.5 mt-1 p-2 rounded-md bg-emerald-50 border border-emerald-200"
        >
          <span className="text-xs font-semibold text-emerald-800">
            All {total} scenes are Complete or Empty.
          </span>
          <span className="text-[11px] text-emerald-800">
            Walk through them to check anything, then hand the set in.
          </span>
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              data-testid="scene-hand-in"
              disabled={busy || !canHandIn}
              title={canHandIn ? 'Record that your scenes are finished' : 'Checking your scenes...'}
              className={`${actionButton} bg-emerald-600 text-white border-emerald-600`}
              onClick={handIn}
            >
              Hand in
            </button>
            <button
              type="button"
              data-testid="scene-review-all"
              className={`${actionButton} bg-white text-neutral-700 border-neutral-300 hover:bg-neutral-50`}
              onClick={reviewAllScenes}
            >
              Review all scenes
            </button>
          </div>
        </div>
      ) : (
        atEnd && (
          <div
            data-testid="scene-end"
            className="flex flex-col gap-1.5 mt-1 p-2 rounded-md bg-amber-50 border border-amber-200"
          >
            <span className="text-[11px] text-amber-800">
              {cycle === 'all'
                ? 'End of the list.'
                : 'No other scene is left to draw; this is the last one.'}{' '}
              {todo + draft} to draw, {skipped} skipped. Hand-in needs every scene Complete or Empty
              (skipped ones included).
            </span>
            {cycle === 'all' && remaining > 0 && (
              <button
                type="button"
                data-testid="scene-go-remaining"
                className={`${actionButton} self-start bg-white text-neutral-700 border-neutral-300 hover:bg-neutral-50`}
                onClick={() => void firstRemainingScene()}
              >
                Go to the first remaining scene
              </button>
            )}
          </div>
        )
      )}
    </div>
  );
}
