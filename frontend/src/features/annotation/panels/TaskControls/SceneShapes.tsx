import { deleteAnnotation, updateAnnotationOpenmode, type AnnotationOut } from '~/api/client';
import { wktToGeometry } from '../../campaign/annotation';
import { handleError } from '~/shared/utils/errorHandler';
import { useCampaign, useLabels } from '../../stores/campaign';
import { buildingScenesOf, useSceneStore, useSceneTaskId } from '../../stores/sceneDrawing';
import { useWorkStore, type Tool } from '../../stores/work';
import { onSceneShapeSaved } from '../../drawing';

const CONFIDENCE_LEVELS = [1, 2, 3, 4, 5];

const chip = (active: boolean) =>
  `px-2 py-1 rounded text-[11px] font-medium border cursor-pointer ${
    active
      ? 'bg-brand-600 text-white border-brand-600'
      : 'bg-white text-neutral-700 border-neutral-300 hover:bg-neutral-50'
  }`;

/** Draw, list, score and delete the separate buildings of the open scene. */
export function SceneShapes() {
  const campaign = useCampaign();
  const sceneTaskId = useSceneTaskId();
  const tool = useWorkStore((s) => s.tool);
  const rows = useSceneStore((s) => s.annotations);
  const outbox = useSceneStore((s) => s.outbox);
  const loadedFor = useSceneStore((s) => s.taskId);
  const error = useSceneStore((s) => s.error);
  const labels = useLabels();
  if (sceneTaskId === null) return null;

  const buildingLabelId = buildingScenesOf(campaign)?.building_label_id;
  const polygonLabel = labels.find((l) => l.id === buildingLabelId) ?? null;
  const shapes = loadedFor === sceneTaskId ? rows : [];
  const unsaved = outbox.filter((o) => o.taskId === sceneTaskId && o.status !== 'saved');

  const pick = (next: Tool) => {
    const work = useWorkStore.getState();
    if (next === 'annotate' && polygonLabel) work.setSelectedLabelId(polygonLabel.id);
    void work.selectTool(next);
  };

  const remove = async (id: number) => {
    try {
      await deleteAnnotation({
        path: { campaign_id: campaign.id, annotation_id: id },
        throwOnError: true,
      });
      useWorkStore.getState().recordDeletes([id]);
    } catch (e) {
      handleError(e, 'Could not delete the building');
    }
  };

  // Patch only the confidence: the other fields are null, which the update
  // endpoint reads as "leave as stored". Re-scoring reopens a finished scene.
  const rate = async (building: AnnotationOut, confidence: number) => {
    const id = building.id;
    try {
      await updateAnnotationOpenmode({
        path: { campaign_id: campaign.id, annotation_id: id },
        body: {
          label_id: null,
          comment: null,
          geometry_wkt: null,
          is_authoritative: null,
          confidence,
        },
        throwOnError: true,
      });
      useWorkStore.getState().recordWrites([
        {
          id,
          labelId: building.label_id,
          geometry: wktToGeometry(building.geometry.geometry),
          origin: 'local',
        },
      ]);
    } catch (e) {
      handleError(e, 'Could not save the confidence');
    }
  };

  return (
    <div
      data-testid="scene-shapes"
      data-scene-task-id={sceneTaskId}
      className="flex flex-col gap-1.5 p-3 border-b border-neutral-100 w-full"
    >
      <span className="font-semibold text-neutral-700 text-xs tracking-wide">
        Buildings ({shapes.length})
      </span>
      <div className="flex gap-1.5">
        <button
          type="button"
          data-testid="scene-tool-draw"
          className={chip(tool === 'annotate')}
          disabled={!polygonLabel}
          onClick={() => pick('annotate')}
        >
          Draw {polygonLabel?.name ?? 'building'}
        </button>
        <button
          type="button"
          data-testid="scene-tool-edit"
          className={chip(tool === 'edit')}
          onClick={() => pick('edit')}
        >
          Edit
        </button>
        <button
          type="button"
          data-testid="scene-tool-pan"
          className={chip(tool === 'pan')}
          onClick={() => pick('pan')}
        >
          Pan
        </button>
      </div>
      {error && <span className="text-[11px] text-red-600">{error}</span>}
      {unsaved.length > 0 && (
        <ul className="flex flex-col gap-1">
          {unsaved.map((o) => (
            <li
              key={o.key}
              data-testid="scene-shape-unsaved"
              data-status={o.status}
              className="flex items-center justify-between gap-1 text-[11px]"
            >
              <span className={o.status === 'failed' ? 'text-red-600' : 'text-neutral-500'}>
                {o.status === 'failed' ? `Not saved (${o.error ?? 'error'})` : 'Saving...'} -
                confidence {o.confidence}
              </span>
              {o.status === 'failed' && (
                <span className="flex gap-2">
                  <button
                    type="button"
                    className="text-brand-700 hover:underline"
                    onClick={() =>
                      useSceneStore.getState().retry(campaign.id, o.key, onSceneShapeSaved)
                    }
                  >
                    Retry
                  </button>
                  <button
                    type="button"
                    className="text-red-600 hover:underline"
                    onClick={() => useSceneStore.getState().discardFailed(o.key)}
                  >
                    Discard
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      <ul className="flex flex-col gap-1">
        {shapes.map((a) => (
          <li
            key={a.id}
            data-testid="scene-shape"
            data-annotation-id={a.id}
            data-origin-task-id={a.origin_task_id ?? ''}
            data-confidence={a.confidence ?? ''}
            className="flex flex-col gap-1 text-[11px] text-neutral-700"
          >
            <div className="flex items-center justify-between">
              <span>
                #{a.id} {labels.find((l) => l.id === a.label_id)?.name ?? 'Unlabelled'}
              </span>
              <button
                type="button"
                data-testid="scene-shape-delete"
                className="text-red-600 hover:underline"
                onClick={() => void remove(a.id)}
              >
                Delete
              </button>
            </div>
            <div className="flex items-center gap-1" title="Confidence for this building">
              <span className="text-neutral-500">Confidence</span>
              {CONFIDENCE_LEVELS.map((level) => (
                <button
                  key={level}
                  type="button"
                  data-testid="scene-shape-confidence"
                  data-level={level}
                  aria-pressed={a.confidence === level}
                  className={`w-5 h-5 rounded border text-[10px] ${
                    a.confidence === level
                      ? 'bg-brand-600 text-white border-brand-600'
                      : 'bg-white border-neutral-300 hover:bg-neutral-50'
                  }`}
                  onClick={() => void rate(a, level)}
                >
                  {level}
                </button>
              ))}
              {a.confidence == null && <span className="text-neutral-400">unrated</span>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
