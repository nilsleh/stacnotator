import { describe, expect, it } from 'vitest';
import { sceneProgress, sceneStatus, sceneStep } from './sceneNav';

const ME = 'me';
type Mark = 'todo' | 'skipped' | 'done' | 'other';

const scene = (mark: Mark) => ({
  annotations:
    mark === 'todo'
      ? []
      : [
          {
            created_by_user_id: mark === 'other' ? 'someone' : ME,
            label_id: mark === 'skipped' ? null : 1,
          },
        ],
});
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- only the fields sceneNav reads
const scenes = (...marks: Mark[]) => marks.map(scene) as any[];

describe('sceneStatus', () => {
  it('reads the current user only', () => {
    expect(sceneStatus(scene('todo') as never, ME)).toBe('todo');
    expect(sceneStatus(scene('other') as never, ME)).toBe('todo');
    expect(sceneStatus(scene('skipped') as never, ME)).toBe('skipped');
    expect(sceneStatus(scene('done') as never, ME)).toBe('done');
  });
});

describe('sceneProgress', () => {
  it('counts each outcome', () => {
    expect(sceneProgress(scenes('done', 'skipped', 'todo', 'done'), ME)).toEqual({
      total: 4,
      done: 2,
      skipped: 1,
      todo: 1,
    });
  });
});

describe('sceneStep', () => {
  it('walks all scenes without wrapping', () => {
    const list = scenes('done', 'todo', 'done');
    expect(sceneStep(list, 0, 1, 'all', ME)).toBe(1);
    expect(sceneStep(list, 2, 1, 'all', ME)).toBeNull();
    expect(sceneStep(list, 0, -1, 'all', ME)).toBeNull();
  });

  it('skips done scenes and wraps over the remaining ones', () => {
    const list = scenes('todo', 'done', 'done', 'todo');
    expect(sceneStep(list, 0, 1, 'remaining', ME)).toBe(3);
    expect(sceneStep(list, 3, 1, 'remaining', ME)).toBe(0);
    expect(sceneStep(list, 3, -1, 'remaining', ME)).toBe(0);
  });

  it('brings skipped scenes back only once nothing untouched is left', () => {
    expect(sceneStep(scenes('done', 'skipped', 'done', 'todo'), 0, 1, 'remaining', ME)).toBe(3);
    expect(sceneStep(scenes('done', 'skipped', 'done', 'done'), 3, 1, 'remaining', ME)).toBe(1);
  });

  it('ends when the current scene was the last one left', () => {
    expect(sceneStep(scenes('done', 'done', 'done'), 2, 1, 'remaining', ME)).toBeNull();
    expect(sceneStep(scenes('done', 'skipped', 'done'), 1, 1, 'remaining', ME)).toBeNull();
  });
});
