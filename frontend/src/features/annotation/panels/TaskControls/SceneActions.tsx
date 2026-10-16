import type { AnnotationTaskOut } from '~/api/client';
import { useCampaign, useCampaignStore } from '../../stores/campaign';
import { buildingScenesOf, useSceneStore } from '../../stores/sceneDrawing';
import { useTasksStore } from '../../stores/tasks';
import {
  finishScene,
  markCurrentSceneDraft,
  skipCurrent,
  taskLabellingPolicy,
} from '../../taskActions';

type Outcome = 'complete' | 'empty' | 'skipped' | null;

const button =
  'inline-flex items-center justify-center h-8 px-3 text-xs font-medium rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap';
const primary = `${button} bg-brand-600 text-white hover:bg-brand-700 shadow-sm disabled:bg-neutral-300 disabled:text-neutral-500 disabled:shadow-none`;
const secondary = `${button} text-neutral-700 bg-white border border-neutral-300 shadow-sm hover:bg-neutral-50`;

const OUTCOME_TEXT: Record<Exclude<Outcome, null>, string> = {
  complete: 'Complete',
  empty: 'Empty',
  skipped: 'Skipped',
};

/**
 * How a scene is finished, in place of the label/confidence/submit block:
 * Complete once its buildings are drawn, Mark empty when there are none, Skip
 * to come back later, Draft to take an outcome back off it.
 */
export function SceneActions({ task }: { task: AnnotationTaskOut }) {
  const campaign = useCampaign();
  const config = buildingScenesOf(campaign);
  const currentUserId = useCampaignStore((s) => s.currentUserId);
  const isSubmitting = useTasksStore((s) => s.isSubmitting);
  const loadedFor = useSceneStore((s) => s.taskId);
  const rows = useSceneStore((s) => s.annotations);
  const outbox = useSceneStore((s) => s.outbox);
  const pending = useSceneStore((s) => s.pending);
  if (!config) return null;

  const mine = task.annotations.find((a) => a.created_by_user_id === currentUserId);
  const outcome: Outcome = !mine
    ? null
    : mine.label_id === config.complete_label_id
      ? 'complete'
      : mine.label_id === config.empty_label_id
        ? 'empty'
        : mine.label_id == null
          ? 'skipped'
          : null;

  const saved = loadedFor === task.id ? rows : [];
  const unscored = saved.filter((a) => a.confidence == null).length;
  const inFlight =
    outbox.filter((o) => o.taskId === task.id && o.status !== 'saved').length +
    (pending?.taskId === task.id ? 1 : 0);
  const buildings = saved.length + inFlight;
  const { mayLabel } = taskLabellingPolicy(task);
  const busy = isSubmitting || !mayLabel;

  const completeBlocker =
    inFlight > 0
      ? 'Wait until every building is scored and saved'
      : saved.length === 0
        ? 'Draw at least one building, or mark the scene empty'
        : unscored > 0
          ? `Score every building first (${unscored} unscored)`
          : null;
  const emptyBlocker =
    buildings > 0 ? `Delete the ${buildings} building(s) to mark the scene empty` : null;

  return (
    <div
      data-testid="scene-actions"
      data-outcome={outcome ?? (buildings > 0 ? 'draft' : 'todo')}
      className="flex flex-col gap-2 p-3 border-r border-b border-neutral-100 flex-1 min-w-[10rem]"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-neutral-500">
          Scene
        </span>
        <span data-testid="scene-outcome" className="text-[11px] font-semibold text-neutral-700">
          {outcome ? OUTCOME_TEXT[outcome] : buildings > 0 ? 'Draft' : 'To do'}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          data-testid="scene-complete"
          disabled={busy || completeBlocker !== null}
          title={completeBlocker ?? 'Every building in this scene is drawn'}
          onClick={() => void finishScene('complete')}
          className={`${primary} flex-1`}
        >
          {outcome === 'complete' ? 'Complete ✓' : 'Complete'}
        </button>
        <button
          type="button"
          data-testid="scene-empty"
          disabled={busy || emptyBlocker !== null}
          title={emptyBlocker ?? 'This scene has no buildings'}
          onClick={() => void finishScene('empty')}
          className={`${secondary} flex-1`}
        >
          {outcome === 'empty' ? 'Empty ✓' : 'Mark empty'}
        </button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          data-testid="scene-skip"
          disabled={busy}
          title="Come back to this scene later; its buildings are kept"
          onClick={() => void skipCurrent()}
          className={`${secondary} flex-1`}
        >
          Skip
        </button>
        <button
          type="button"
          data-testid="scene-draft"
          disabled={busy || outcome === null}
          title="Take the outcome off this scene; its buildings stay as a draft"
          onClick={() => void markCurrentSceneDraft()}
          className={`${secondary} flex-1`}
        >
          Draft
        </button>
      </div>
      {completeBlocker && saved.length > 0 && (
        <span className="text-[11px] text-neutral-500">{completeBlocker}.</span>
      )}
    </div>
  );
}
