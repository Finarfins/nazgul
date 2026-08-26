import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';

const {get}=vi.hoisted(()=>({get:vi.fn()}));

vi.mock('../api',()=>({
 api:{get},
 errorDetail:(_err:unknown,fallback:string)=>fallback,
 money:(value:unknown)=>`₺${Number(value||0).toFixed(2)}`,
}));

const belge=(id:number,tarih:string)=>({
 id,document_no:`SAT-${id}`,transaction_date:tarih,final_total:100,status:'completed',
});

const YILLAR=[
 {year:'2026',count:8,total:800},
 {year:'2025',count:9,total:900},
 {year:'2024',count:13,total:1300},
];

/** Açılış isteği (limit=1) yıl özetini döndürür. */
const ozetYaniti=(years=YILLAR)=>({data:{items:[],total:30,has_more:true,year:null,years}});
const yilYaniti=(items:any[],hasMore=false)=>({data:{items,total:items.length,has_more:hasMore,year:'x',years:YILLAR}});

const goster=async(props?:Partial<{entityType:'customer'|'supplier';entityId:number}>)=>{
 const {default:EntityDocumentList}=await import('./EntityDocumentList');
 return render(<EntityDocumentList
  entityType={props?.entityType||'customer'}
  entityId={props?.entityId??75}
  onOpenDocument={()=>{}}
 />);
};

describe('EntityDocumentList — yıl gruplama',()=>{
 beforeEach(()=>{vi.resetModules();get.mockReset()});
 afterEach(cleanup);

 it('yıl başlıklarını gösterir ve yalnız en yeni yılı açar',async()=>{
  get.mockResolvedValueOnce(ozetYaniti());
  get.mockResolvedValueOnce(yilYaniti([belge(1,'2026-07-13')]));
  await goster();

  // Üç yıl da başlık olarak görünür; sayfa uzamaz.
  expect(await screen.findByText('2026')).toBeInTheDocument();
  expect(screen.getByText('2025')).toBeInTheDocument();
  expect(screen.getByText('2024')).toBeInTheDocument();
  expect(screen.getByText('13 belge')).toBeInTheDocument();

  // Yalnız açılan yılın satırları indirilir.
  await waitFor(()=>expect(get).toHaveBeenLastCalledWith(
   '/customers/75/documents',{params:{offset:0,limit:50,year:'2026'}},
  ));
  expect(await screen.findByText('SAT-1')).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(2);
 });

 it('kapalı bir yıla tıklayınca o yılın belgelerini çeker',async()=>{
  get.mockResolvedValueOnce(ozetYaniti());
  get.mockResolvedValueOnce(yilYaniti([belge(1,'2026-07-13')]));
  await goster();
  await screen.findByText('SAT-1');

  get.mockResolvedValueOnce(yilYaniti([belge(90,'2024-04-02')]));
  fireEvent.click(screen.getByText('2024'));

  await waitFor(()=>expect(get).toHaveBeenLastCalledWith(
   '/customers/75/documents',{params:{offset:0,limit:50,year:'2024'}},
  ));
  expect(await screen.findByText('SAT-90')).toBeInTheDocument();
  expect(screen.getByText('2024-04-02')).toBeInTheDocument();
 });

 it('aynı yıl tekrar açılınca yeniden istek atmaz',async()=>{
  get.mockResolvedValueOnce(ozetYaniti());
  get.mockResolvedValueOnce(yilYaniti([belge(1,'2026-07-13')]));
  await goster();
  await screen.findByText('SAT-1');
  const cagri=get.mock.calls.length;

  fireEvent.click(screen.getByText('2026')); // kapat
  fireEvent.click(screen.getByText('2026')); // tekrar aç
  await waitFor(()=>expect(screen.getByText('SAT-1')).toBeInTheDocument());
  expect(get.mock.calls.length).toBe(cagri);
 });

 it('yıl içinde sayfalar; ikinci sayfa doğru offset ile gelir',async()=>{
  get.mockResolvedValueOnce(ozetYaniti());
  get.mockResolvedValueOnce(yilYaniti([belge(1,'2026-07-13'),belge(2,'2026-07-08')],true));
  await goster();
  await screen.findByText('SAT-1');

  get.mockResolvedValueOnce(yilYaniti([belge(3,'2026-06-01')]));
  fireEvent.click(screen.getByRole('button',{name:/Daha fazla göster/}));

  await waitFor(()=>expect(get).toHaveBeenLastCalledWith(
   '/customers/75/documents',{params:{offset:2,limit:50,year:'2026'}},
  ));
  // Yeni sayfa eskisinin üstüne eklenir.
  expect(await screen.findByText('SAT-3')).toBeInTheDocument();
  expect(screen.getByText('SAT-1')).toBeInTheDocument();
 });

 it('tedarikçide alış ucunu kullanır',async()=>{
  get.mockResolvedValueOnce({data:{items:[],total:0,has_more:false,years:[]}});
  await goster({entityType:'supplier',entityId:12});
  await waitFor(()=>expect(get).toHaveBeenCalledWith(
   '/suppliers/12/documents',{params:{offset:0,limit:1}},
  ));
  expect(await screen.findByText('Kayıt yok.')).toBeInTheDocument();
 });

 it('hata durumunda uyarı gösterir',async()=>{
  get.mockImplementationOnce(()=>Promise.reject(new Error('kopuk')));
  await goster();
  expect(await screen.findByText('Belgeler yüklenemedi.')).toBeInTheDocument();
 });
});
