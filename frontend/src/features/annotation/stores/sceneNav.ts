import { create } from 'zustand';
import type { AnnotationTaskOut, BuildingScenes } from '~/api/client';
import { ALL_TASK_STATUSES, applyTaskFilter, type TaskFilter } from '../campaign/tasks';
import { campaignState, useCampaignStore } from './campaign';
import { buildingScenesOf } from './sceneDrawing';
import { currentTask, useTasksStore } from './tasks';

/** A scene, from the current annotator's point of view. Skipping is not done:
 *  a skipped scene stays in the remaining cycle and blocks hand-in. */
export type SceneStatus = 'todo' | 'skipped' | 'done';

type SceneTaskLike = Pick<AnnotationTaskOut, 'annotations'>;

/** The user's own task annotation, which is a scene's outcome. */
export const myAnnotation = (task: SceneTaskLike, userId: string | null) =>
  task.annotations.find((a) => a.created_by_user_id === userId);

export function sceneStatus(task: SceneTaskLike, userId: string | null): SceneStatus {
  const mine = myAnnotation(task, userId);
  if (!mine) return 'todo';
  return mine.label_id == null ? 'skipped' : 'done';
}

/** The outcome the user gave a scene, or null while it has none. */
export function sceneOutcome(
  task: SceneTaskLike,
  userId: string | null,
  config: BuildingScenes
): 'complete' | 'empty' | 'skipped' | null {
  const mine = myAnnotation(task, userId);
  if (!mine) return null;
  if (mine.label_id === config.complete_label_id) return 'complete';
  if (mine.label_id === config.empty_label_id) return 'empty';
  return mine.label_id == null ? 'skipped' : null;
}

export interface SceneProgress {
  total: number;
  done: number;
  skipped: number;
  todo: number;
}

export function sceneProgress(tasks: SceneTaskLike[], userId: string | null): SceneProgress {
  const progress = { total: tasks.length, done: 0, skipped: 0, todo: 0 };
  for (const task of tasks) progress[sceneStatus(task, userId)] += 1;
  return progress;
}

export type SceneCycle = 'remaining' | 'all';

/**
 * Where Next (dir 1) or Prev (dir -1) goes from `current`, or null at the end.
 *
 * 'all' walks the list in order and stops at either end instead of wrapping,
 * so running off the end is visible. 'remaining' wraps, but only over scenes
 * that are not done and never back onto the current one: untouched scenes
 * first, then the skipped ones, so skips come back once the rest is drawn.
 */
export function sceneStep(
  tasks: SceneTaskLike[],
  current: number,
  dir: 1 | -1,
  cycle: SceneCycle,
  userId: string | null
): number | null {
  if (cycle === 'all') {
    const target = current + dir;
    return target >= 0 && target < tasks.length ? target : null;
  }
  for (const wanted of ['todo', 'skipped'] as const) {
    for (let step = 1; step < tasks.length; step += 1) {
      const index = (((current + dir * step) % tasks.length) + tasks.length) % tasks.length;
      if (sceneStatus(tasks[index], userId) === wanted) return index;
    }
  }
  return null;
}

/** The filter with every status in, so done and skipped scenes stay reachable
 *  in the same order as the ones still to draw. */
const widened = (filter: TaskFilter): TaskFilter => ({
  ...filter,
  statuses: [...ALL_TASK_STATUSES],
});

/** The annotator's scenes: the current filter's scope at every status. */
export function sceneScope(
  allTasks: AnnotationTaskOut[],
  filter: TaskFilter,
  userId: string | null
): AnnotationTaskOut[] {
  return applyTaskFilter(allTasks, widened(filter), userId, Date.now()).visibleTasks;
}

interface SceneNavState {
  cycle: SceneCycle;
  /** The scene Next/Prev ran out on; the end card shows while it is current. */
  endAt: number | null;
  setCycle: (cycle: SceneCycle) => void;
  clearEnd: () => void;
}

export const useSceneNavStore = create<SceneNavState>((set) => ({
  cycle: 'remaining',
  endAt: null,
  setCycle: (cycle) => set({ cycle, endAt: null }),
  clearEnd: () => set({ endAt: null }),
}));

/** True when the current task is a scene, so navigation goes through here. */
export function onSceneTask(): boolean {
  return currentTask() !== null && buildingScenesOf(useCampaignStore.getState().campaign) !== null;
}

/** Widen the visible list to every status, keeping the current scene current. */
function ensureWidened(): void {
  const { catalog } = campaignState();
  const tasks = useTasksStore.getState();
  const missing = ALL_TASK_STATUSES.some((s) => !tasks.filter.statuses.includes(s));
  if (!missing) return;
  const current = currentTask();
  tasks.setFilter(widened(tasks.filter), Date.now(), catalog);
  if (current) useTasksStore.getState().goToAnnotationNumber(current.annotation_number, catalog);
}

/** Next/Prev between scenes. Returns false when there is nowhere to go, which
 *  raises the end card on the current scene. */
export function stepScene(dir: 1 | -1): boolean {
  ensureWidened();
  const { catalog } = campaignState();
  const { currentUserId } = useCampaignStore.getState();
  const tasks = useTasksStore.getState();
  const nav = useSceneNavStore.getState();
  const target = sceneStep(tasks.visibleTasks, tasks.currentIndex, dir, nav.cycle, currentUserId);
  if (target === null) {
    useSceneNavStore.setState({ endAt: currentTask()?.id ?? null });
    return false;
  }
  useSceneNavStore.setState({ endAt: null });
  const number = tasks.visibleTasks[target].annotation_number;
  tasks.goToAnnotationNumber(number, catalog);
  return true;
}

/** Jump to the first scene of the scope, walking all of them from there. */
export function reviewAllScenes(): void {
  ensureWidened();
  const { catalog } = campaignState();
  useSceneNavStore.getState().setCycle('all');
  const first = useTasksStore.getState().visibleTasks[0];
  if (first) useTasksStore.getState().goToAnnotationNumber(first.annotation_number, catalog);
}

/** Jump to the first scene still to draw (untouched before skipped). */
export function firstRemainingScene(): boolean {
  ensureWidened();
  const { catalog } = campaignState();
  const { currentUserId } = useCampaignStore.getState();
  useSceneNavStore.getState().setCycle('remaining');
  const list = useTasksStore.getState().visibleTasks;
  const pick =
    list.find((t) => sceneStatus(t, currentUserId) === 'todo') ??
    list.find((t) => sceneStatus(t, currentUserId) === 'skipped');
  if (!pick) return false;
  useTasksStore.getState().goToAnnotationNumber(pick.annotation_number, catalog);
  return true;
}
