import {act,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach,describe,expect,it,vi} from 'vitest';

import {OFFLINE_VERIFICATION_MS} from '../field/store';
import FieldWorkOrders from './FieldWorkOrders';

const fixtures=vi.hoisted(()=>{const verifiedAt=new Date('2026-01-01T00:00:00Z').getTime();const meta={partition:'3:7',companyId:3,companyName:'Firma',userId:7,
 user:{id:7,username:'tech',display_name:'Teknisyen',role:'satis',must_change_password:false},
 permissions:['field_service'],lastOnlineVerifiedAt:verifiedAt,lastObservedAt:verifiedAt,
 lastSyncAt:verifiedAt,snapshotVersion:1};const cached=[{id:1,work_order_no:'WO-1',status:'OPEN',version:'1',updated_at:new Date().toISOString(),
 priority:'NORMAL',scheduled_date:null,complaint:null,diagnosis:null,repair_summary:null,technician_notes:null,
 machine:{id:1,brand:'Sungur',model:'M1',serial_number:null,chassis_number:null,working_hours:null},
 customer:{display_name:'Gizli Müşteri',service_address:null}}];const auth={user:meta.user,activeCompany:{id:3,name:'Firma'}};return{verifiedAt,meta,cached,auth}});

vi.mock('../AuthContext',()=>({useAuth:()=>fixtures.auth}));
vi.mock('../field/snapshotSource',()=>({syncFieldSnapshot:vi.fn().mockRejectedValue(new Error('offline'))}));
vi.mock('../field/fieldOutbox',()=>({
 flushFieldOutbox:vi.fn().mockResolvedValue({sent:0,conflicted:0,pending:0}),
 queueFieldStatus:vi.fn(),
}));
vi.mock('../field/store',async importOriginal=>{
 const actual=await importOriginal<typeof import('../field/store')>();
 // readFieldOutbox/readFieldConflicts de mocklanmak ZORUNDA: `actual` sürümleri
 // gerçek IndexedDB'yi açar ve jsdom'da her çağrıda yakalanmamış hata üretir.
 // Bu test sürenin dolmasını sınıyor, kuyruğu değil; boş dönmeleri yeterli.
 return {...actual,
  readFieldOutbox:vi.fn().mockResolvedValue([]),
  readFieldConflicts:vi.fn().mockResolvedValue([]),
  readFieldAccess:vi.fn().mockImplementation(async()=>Date.now()<fixtures.verifiedAt+actual.OFFLINE_VERIFICATION_MS
   ?{meta:fixtures.meta,workOrders:fixtures.cached}:null)};
});

describe('field expiry gate',()=>{
 beforeEach(()=>{vi.useFakeTimers();vi.setSystemTime(fixtures.verifiedAt)});
 it('locks the already-open screen at the exact expiry instant before data can render',async()=>{
  render(<MemoryRouter initialEntries={['/saha/']}><FieldWorkOrders/></MemoryRouter>);
  await act(async()=>{await Promise.resolve();await Promise.resolve()});
  expect(screen.getByText('Gizli Müşteri')).toBeInTheDocument();
  await act(async()=>{vi.advanceTimersByTime(OFFLINE_VERIFICATION_MS)});
  expect(screen.getByText(/12 saatlik doğrulama süresi doldu/)).toBeInTheDocument();
  expect(screen.queryByText('Gizli Müşteri')).not.toBeInTheDocument();
  vi.useRealTimers();
 });
});
