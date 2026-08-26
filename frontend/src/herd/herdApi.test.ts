import {beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
vi.mock('../api', () => ({
  api: {get: (...args: unknown[]) => get(...args)},
  errorDetail: (_error: unknown, fallback: string) => fallback,
}));

import {fetchAllHerd} from './herdApi';

beforeEach(() => {
  get.mockReset();
});

it('toplam ilk sayfa limitini aşınca ikinci sayfayı da birleştirir', async () => {
  const ilkSayfa = Array.from({length: 200}, (_, i) => ({id: i + 1}));
  get.mockImplementation((_url: string, config?: {params?: {offset?: number}}) => {
    const offset = config?.params?.offset ?? 0;
    return Promise.resolve({
      data: {
        items: offset === 0 ? ilkSayfa : [{id: 201}],
        total: 201,
        limit: 200,
        offset,
      },
    });
  });

  const items = await fetchAllHerd<{id: number}>('/animals');

  expect(items).toHaveLength(201);
  expect(items.at(-1)).toEqual({id: 201});
  expect(get).toHaveBeenNthCalledWith(1, '/animals', {params: {limit: 200, offset: 0}});
  expect(get).toHaveBeenNthCalledWith(2, '/animals', {params: {limit: 200, offset: 200}});
});
