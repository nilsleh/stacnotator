import { create } from 'zustand';
import {
  getBuildingProgress,
  handInBuildings,
  markSceneDraft,
  withdrawBuildingHandIn,
  type BuildingProgressOut,
} from '~/api/client';
import { handleError } from '~/shared/utils/errorHandler';

/**
 * The annotator's scenes as the server counts them: Complete, Empty, Skipped,
 * Draft (buildings but no outcome) and To do, plus their hand-in. Navigation
 * reads outcomes straight off the tasks; this is what tells a draft from an
 * untouched scene and whether hand-in is allowed.
 */
interface BuildingProgressState {
  campaignId: number | null;
  progress: BuildingProgressOut | null;
  /** When the request behind `progress` was sent (client clock). */
  requestedAt: number;
  busy: boolean;
  load: (campaignId: number) => Promise<void>;
  handIn: (campaignId: number) => Promise<boolean>;
  withdraw: (campaignId: number) => Promise<void>;
  /** Clear the user's outcome on a scene, keeping its buildings as a draft. */
  markDraft: (campaignId: number, taskId: number) => Promise<boolean>;
}

let requestSeq = 0;

/** When this page last wrote each scene's outcome (client clock). An answer
 *  requested before that write cannot speak for the outcome. */
const outcomeWrittenAt = new Map<number, number>();

export const noteOutcomeWritten = (taskId: number) => outcomeWrittenAt.set(taskId, Date.now());

export const writtenSince = (taskId: number, requestedAt: number) =>
  (outcomeWrittenAt.get(taskId) ?? 0) >= requestedAt;

export const useBuildingProgressStore = create<BuildingProgressState>((set, get) => {
  const apply = (campaignId: number, progress: BuildingProgressOut, requestedAt: number) =>
    set({ campaignId, progress, requestedAt, busy: false });

  const mutate = async (
    campaignId: number,
    call: () => Promise<{ data: BuildingProgressOut }>,
    failure: string
  ): Promise<boolean> => {
    set({ busy: true });
    try {
      requestSeq += 1;
      const requestedAt = Date.now();
      apply(campaignId, (await call()).data, requestedAt);
      return true;
    } catch (error) {
      set({ busy: false });
      handleError(error, failure);
      return false;
    }
  };

  return {
    campaignId: null,
    progress: null,
    requestedAt: 0,
    busy: false,

    load: async (campaignId) => {
      if (get().campaignId !== campaignId) set({ campaignId, progress: null });
      const seq = ++requestSeq;
      const requestedAt = Date.now();
      try {
        const { data } = await getBuildingProgress({
          path: { campaign_id: campaignId },
          throwOnError: true,
        });
        // A later load or write has the fresher answer.
        if (seq === requestSeq && get().campaignId === campaignId) {
          set({ progress: data, requestedAt });
        }
      } catch (error) {
        if (seq === requestSeq) handleError(error, 'Could not load scene progress');
      }
    },

    handIn: (campaignId) =>
      mutate(
        campaignId,
        () => handInBuildings({ path: { campaign_id: campaignId }, throwOnError: true }),
        'Could not hand in'
      ),

    withdraw: async (campaignId) => {
      await mutate(
        campaignId,
        () => withdrawBuildingHandIn({ path: { campaign_id: campaignId }, throwOnError: true }),
        'Could not withdraw the hand-in'
      );
    },

    markDraft: (campaignId, taskId) =>
      mutate(
        campaignId,
        () =>
          markSceneDraft({
            path: { campaign_id: campaignId, task_id: taskId },
            throwOnError: true,
          }),
        'Could not mark the scene as a draft'
      ),
  };
});
