import { create } from 'zustand';
import {
  createAnnotationOpenmode,
  getSceneBuildings,
  type AnnotationOut,
  type BuildingScenes,
  type CampaignOutFull,
} from '~/api/client';
import { extractErrorMessage } from '~/shared/utils/errorHandler';
import { squareBuilding } from '~/shared/utils/utility';
import { geometryToWkt } from '../campaign/annotation';
import { originTaskId, useCampaignStore } from './campaign';
import { currentTask, useCurrentTask } from './tasks';

/** The labels of a building campaign, or null for any other campaign. In a
 *  building campaign every task is a scene: the annotator draws any number of
 *  separate buildings inside its outline while staying in Tasks mode. Each
 *  building is its own standalone annotation whose `origin_task_id` is the
 *  scene's task; the scene's outcome is the user's task annotation. */
export const buildingScenesOf = (campaign: CampaignOutFull | null): BuildingScenes | null =>
  campaign?.settings.building_scenes ?? null;

/** The scene being drawn in, or null outside Tasks mode or off a scene task. */
export function sceneTaskId(): number | null {
  const { workMode, campaign } = useCampaignStore.getState();
  const task = currentTask();
  return workMode === 'tasks' && task && buildingScenesOf(campaign) ? task.id : null;
}

export function useSceneTaskId(): number | null {
  const workMode = useCampaignStore((s) => s.workMode);
  const campaign = useCampaignStore((s) => s.campaign);
  const task = useCurrentTask();
  return workMode === 'tasks' && task && buildingScenesOf(campaign) ? task.id : null;
}

/** Sent with every standalone create: the scene in Tasks mode, else the task
 *  Explore was entered from. A building campaign never links an Explore shape
 *  to a scene: only scene mode adds buildings to one. */
export const annotationOriginTaskId = (): number | undefined => {
  const scene = sceneTaskId();
  if (scene !== null) return scene;
  return buildingScenesOf(useCampaignStore.getState().campaign) ? undefined : originTaskId();
};

interface SceneState {
  taskId: number | null;
  annotations: AnnotationOut[];
  loading: boolean;
  error: string | null;
  /** A finished building waiting for its confidence. Nothing is stored until
   *  it gets one, and drawing pauses until it is scored or discarded. */
  pending: PendingShape | null;
  /** Scored buildings on their way to the server. Scoring hands the building
   *  over here at once, so the next one can be drawn without waiting for the save. */
  outbox: OutboxShape[];
  load: (campaignId: number, taskId: number) => Promise<void>;
  clear: () => void;
  setPending: (pending: PendingShape | null) => void;
  /** Square the corners of the building waiting for its score. */
  squarePending: () => void;
  /** Score the pending building and save it in the background. */
  score: (campaignId: number, confidence: number, onSaved: OnSaved) => void;
  retry: (campaignId: number, key: string, onSaved: OnSaved) => void;
  discardFailed: (key: string) => void;
}

export interface PendingShape {
  taskId: number;
  labelId: number;
  geometry: GeoJSON.Geometry;
}

export interface OutboxShape extends PendingShape {
  key: string;
  confidence: number;
  /** 'saved' lingers until the scene list carries the id, so the building
   *  never blinks out between the save and the reload. */
  status: 'saving' | 'failed' | 'saved';
  savedId: number | null;
  error: string | null;
}

type OnSaved = (shape: OutboxShape & { savedId: number }) => void;

let outboxSeq = 0;

export const useSceneStore = create<SceneState>((set, get) => {
  const patch = (key: string, change: Partial<OutboxShape>) =>
    set((s) => ({ outbox: s.outbox.map((o) => (o.key === key ? { ...o, ...change } : o)) }));

  const send = async (campaignId: number, key: string, onSaved: OnSaved) => {
    const shape = get().outbox.find((o) => o.key === key);
    if (!shape) return;
    patch(key, { status: 'saving', error: null });
    try {
      const result = await createAnnotationOpenmode({
        path: { campaign_id: campaignId },
        body: {
          label_id: shape.labelId,
          comment: null,
          geometry_wkt: geometryToWkt(shape.geometry),
          confidence: shape.confidence,
          form_values: null,
          origin_task_id: shape.taskId,
        },
        throwOnError: true,
      });
      patch(key, { status: 'saved', savedId: result.data.id });
      onSaved({ ...shape, status: 'saved', savedId: result.data.id });
    } catch (error) {
      patch(key, { status: 'failed', error: extractErrorMessage(error) });
    }
  };

  return {
    taskId: null,
    annotations: [],
    loading: false,
    error: null,
    pending: null,
    outbox: [],

    load: async (campaignId, taskId) => {
      if (get().taskId !== taskId) set({ taskId, annotations: [], error: null });
      set({ loading: true });
      try {
        const result = await getSceneBuildings({
          path: { campaign_id: campaignId, task_id: taskId },
          throwOnError: true,
        });
        // A newer scene may have been opened while this one loaded.
        if (get().taskId !== taskId) return;
        const listed = new Set(result.data.map((a) => a.id));
        set((s) => ({
          annotations: result.data,
          loading: false,
          outbox: s.outbox.filter((o) => !(o.status === 'saved' && listed.has(o.savedId ?? -1))),
        }));
      } catch (error) {
        if (get().taskId === taskId) set({ loading: false, error: extractErrorMessage(error) });
      }
    },

    clear: () => set({ taskId: null, annotations: [], loading: false, error: null, pending: null }),
    setPending: (pending) => set({ pending }),
    squarePending: () =>
      set((s) =>
        s.pending ? { pending: { ...s.pending, geometry: squareBuilding(s.pending.geometry) } } : {}
      ),

    score: (campaignId, confidence, onSaved) => {
      const pending = get().pending;
      if (!pending) return;
      const key = `shape-${++outboxSeq}`;
      set((s) => ({
        pending: null,
        outbox: [
          ...s.outbox,
          { ...pending, key, confidence, status: 'saving', savedId: null, error: null },
        ],
      }));
      void send(campaignId, key, onSaved);
    },

    retry: (campaignId, key, onSaved) => void send(campaignId, key, onSaved),

    discardFailed: (key) =>
      set((s) => ({ outbox: s.outbox.filter((o) => o.key !== key || o.status !== 'failed') })),
  };
});

/** Buildings of this scene not yet safely stored: scoring, saving or failed. */
export const unsavedIn = (
  { pending, outbox }: Pick<SceneState, 'pending' | 'outbox'>,
  taskId: number
): number =>
  outbox.filter((o) => o.taskId === taskId && o.status !== 'saved').length +
  (pending?.taskId === taskId ? 1 : 0);

export const unsavedBuildings = (taskId: number): number =>
  unsavedIn(useSceneStore.getState(), taskId);
