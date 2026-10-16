import { useHotkeys, type Binding } from '../../hotkeys';
import { onSceneShapeSaved } from '../../drawing';
import { useCampaign } from '../../stores/campaign';
import { useSceneStore } from '../../stores/sceneDrawing';

const LEVELS = [1, 2, 3, 4, 5];

const discardPending = () => useSceneStore.getState().setPending(null);

/**
 * Asked the moment a building is finished: a digit (or a click) scores it,
 * Q squares its corners first, Escape throws it away. The save runs in the background, so the next
 * building can be drawn straight away. Mounted only while a shape is pending,
 * so its digits outrank the task panel's label digits for exactly that long.
 */
export function SceneScorePrompt() {
  const campaign = useCampaign();
  const pending = useSceneStore((s) => s.pending);
  const score = (level: number) =>
    useSceneStore.getState().score(campaign.id, level, onSceneShapeSaved);

  const bindings: Binding[] = [
    ...LEVELS.map((level) => ({ key: String(level), run: () => score(level) })),
    { key: 'q', run: () => useSceneStore.getState().squarePending() },
    { key: 'escape', run: discardPending },
  ];
  useHotkeys(bindings, [campaign.id]);

  if (!pending) return null;
  return (
    <div
      data-testid="scene-score-prompt"
      className="flex flex-col gap-1.5 p-2 rounded-md bg-white shadow-lg border border-neutral-200 text-[11px] text-neutral-700"
    >
      <span className="font-semibold">Confidence for this building</span>
      <div className="flex gap-1">
        {LEVELS.map((level) => (
          <button
            key={level}
            type="button"
            data-testid="scene-score-option"
            data-level={level}
            className="w-6 h-6 rounded border border-neutral-300 bg-white hover:bg-brand-600 hover:text-white"
            onClick={() => score(level)}
          >
            {level}
          </button>
        ))}
      </div>
      <button
        type="button"
        data-testid="scene-score-square"
        className="self-start px-2 h-6 rounded border border-neutral-300 bg-white hover:bg-neutral-50"
        onClick={() => useSceneStore.getState().squarePending()}
      >
        Square corners (Q)
      </button>
      <span className="text-neutral-500">Press 1-5, Q to square, or Esc to discard</span>
    </div>
  );
}
