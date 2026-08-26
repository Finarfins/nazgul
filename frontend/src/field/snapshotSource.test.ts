import {beforeEach,describe,expect,it,vi} from 'vitest';

const apiGet=vi.hoisted(()=>vi.fn());
const capture=vi.hoisted(()=>vi.fn());
const apply=vi.hoisted(()=>vi.fn());
vi.mock('../api',()=>({api:{get:apiGet}}));
vi.mock('./fieldDatabase',()=>({captureWriteEpoch:capture,applySnapshot:apply}));

import {abortFieldSyncs} from './sessionCoordinator';
import {syncFieldSnapshot} from './snapshotSource';

describe('field sync cancellation',()=>{
 beforeEach(()=>{vi.clearAllMocks();capture.mockResolvedValue(4)});
 it('aborts an in-flight sync before any partial snapshot write',async()=>{
  apiGet.mockImplementation((_url:string,{signal}:{signal:AbortSignal})=>new Promise((_resolve,reject)=>{
   signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));
  }));
  const sync=syncFieldSnapshot(1,7);await Promise.resolve();abortFieldSyncs();
  await expect(sync).rejects.toMatchObject({name:'AbortError'});
  expect(apply).not.toHaveBeenCalled();
 });
});
