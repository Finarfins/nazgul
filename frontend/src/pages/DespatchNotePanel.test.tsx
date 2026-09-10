import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {api} from '../api';
import {DespatchNotePanel,type EDespatchStatus} from './DespatchNotePanel';

vi.mock('../api',()=>({
 api:{get:vi.fn(),post:vi.fn()},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
 money:(value:unknown)=>String(value??''),
}));

const ETTN='11111111-2222-3333-4444-555555555555';

const irsaliye=(status:EDespatchStatus,lastError:string|null=null)=>({
 id:7,invoice_id:17,despatch_uuid:ETTN,despatch_number:'IRS-FTR-1',
 edespatch_status:status,edespatch_gib_status_code:null,
 edespatch_provider_uuid:null,edespatch_last_error:lastError,
 driver_name:'Ahmet Yilmaz',vehicle_plate:'34ABC123',
});

const liste=(items:unknown[])=>({data:{items,total:items.length}});

describe('InvoiceDetail e-İrsaliye paneli',()=>{
 beforeEach(()=>vi.clearAllMocks());
 afterEach(()=>cleanup());

 it.each([
  ['NONE','e-İrsaliye: gönderilmedi'],
  ['QUEUED','e-İrsaliye: kuyrukta'],
  ['PROCESSING','e-İrsaliye: işleniyor'],
  ['SIGNED','e-İrsaliye: imzalandı'],
  ['SENT','e-İrsaliye: gönderildi'],
  ['DELIVERED','e-İrsaliye: alındı'],
  ['FAILED','e-İrsaliye: başarısız'],
  ['UNKNOWN','e-İrsaliye: durum bilinmiyor'],
 ] as const)('%s durumunu doğru rozetle gösterir',async(status,label)=>{
  vi.mocked(api.get).mockResolvedValue(liste([irsaliye(status)]) as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith('/despatch-notes?invoice_id=17');
 });

 it('irsaliye yokken OLUSTUR butonu gosterir, gonder/sorgula GOSTERMEZ',async()=>{
  vi.mocked(api.get).mockResolvedValue(liste([]) as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByRole('button',{name:/e-İrsaliye Oluştur/})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Gönder'})).toBeNull();
  expect(screen.queryByRole('button',{name:/Durumu Sorgula/})).toBeNull();
 });

 it('UNKNOWN durumda GONDER kapali ve uyari gorunuyor',async()=>{
  // Sunucudaki `edespatch.GONDERIM_KAPALI` ile AYNI kural. Buton acik
  // olsaydi kullanici her basista 409 alirdi; kapali olmasi cift irsaliye
  // riskini kullaniciya ANLATIYOR.
  vi.mocked(api.get).mockResolvedValue(liste([irsaliye('UNKNOWN')]) as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByText('e-İrsaliye: durum bilinmiyor')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Gönder'})).toBeDisabled();
  expect(screen.getByText(/Çift irsaliye riskine karşı/)).toBeInTheDocument();
  // Cikis yolu ACIK: sorgulama butonu etkin.
  expect(screen.getByRole('button',{name:/Durumu Sorgula/})).toBeEnabled();
 });

 it.each(['NONE','FAILED'] as const)(
  '%s durumunda GONDER acik',
  async status=>{
   vi.mocked(api.get).mockResolvedValue(liste([irsaliye(status)]) as never);
   render(<DespatchNotePanel invoiceId={17}/>);
   await screen.findByText(/e-İrsaliye:/);
   expect(screen.getByRole('button',{name:'Gönder'})).toBeEnabled();
  },
 );

 it.each(['QUEUED','SENT','DELIVERED'] as const)(
  '%s durumunda GONDER kapali',
  async status=>{
   vi.mocked(api.get).mockResolvedValue(liste([irsaliye(status)]) as never);
   render(<DespatchNotePanel invoiceId={17}/>);
   await screen.findByText(/e-İrsaliye:/);
   expect(screen.getByRole('button',{name:'Gönder'})).toBeDisabled();
  },
 );

 it('PDF butonu HIC YOK — saglayici sozlesmesi dogrulanmadi',async()=>{
  // Uc 501 donuyor (kesif §2.4). Gorunmeyen bir buton, her basista hata
  // veren bir butondan iyidir.
  vi.mocked(api.get).mockResolvedValue(liste([irsaliye('SENT')]) as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  await screen.findByText('e-İrsaliye: gönderildi');
  expect(screen.getByRole('link',{name:'XML'})).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:'PDF'})).toBeNull();
  expect(screen.queryByRole('button',{name:'PDF'})).toBeNull();
 });

 it('dialog ZORUNLU alanlar dolana kadar OLUSTUR kapali tutuyor',async()=>{
  vi.mocked(api.get).mockResolvedValue(liste([]) as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:/e-İrsaliye Oluştur/}));

  expect(screen.getByRole('button',{name:'Oluştur'})).toBeDisabled();

  const yaz=(etiket:RegExp,deger:string)=>
   fireEvent.change(screen.getByLabelText(etiket),{target:{value:deger}});
  yaz(/Fiili Sevk Zamanı/,'2026-09-13T08:30');
  yaz(/Şoför Adı Soyadı/,'Ahmet Yilmaz');
  // ONCE EKSIK TCKN: 10 hane yetmez, 11 gerekir.
  yaz(/Şoför T\.C\. Kimlik No/,'1111111111');
  yaz(/Araç Plakası/,'34ABC123');
  yaz(/Teslim Adresi/,'Depo Yolu 7');
  expect(screen.getByRole('button',{name:'Oluştur'})).toBeDisabled();

  yaz(/Şoför T\.C\. Kimlik No/,'11111111110');
  expect(screen.getByRole('button',{name:'Oluştur'})).toBeEnabled();
 });

 it('OLUSTUR govdeyi dogru gonderiyor ve bos dorseyi NULL yapiyor',async()=>{
  vi.mocked(api.get).mockResolvedValue(liste([]) as never);
  vi.mocked(api.post).mockResolvedValue({data:irsaliye('NONE')} as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:/e-İrsaliye Oluştur/}));

  const yaz=(etiket:RegExp,deger:string)=>
   fireEvent.change(screen.getByLabelText(etiket),{target:{value:deger}});
  yaz(/Fiili Sevk Zamanı/,'2026-09-13T08:30');
  yaz(/Şoför Adı Soyadı/,'Ahmet Yilmaz');
  yaz(/Şoför T\.C\. Kimlik No/,'11111111110');
  yaz(/Araç Plakası/,'34ABC123');
  yaz(/Teslim Adresi/,'Depo Yolu 7');
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/despatch-notes',{
   invoice_id:17,
   actual_shipment_at:'2026-09-13T08:30',
   driver_name:'Ahmet Yilmaz',
   driver_national_id:'11111111110',
   vehicle_plate:'34ABC123',
   // BOS DORSE `null` gider, bos dize DEGIL: bos dize saklanirsa UBL'de
   // bos bir `DORSEPLAKA` elemani dogardi.
   trailer_plate:null,
   delivery_address:'Depo Yolu 7',
  }));
  expect(await screen.findByText(/kaydı oluşturuldu/)).toBeInTheDocument();
 });

 it('GONDER ve SORGULA dogru uclari cagiriyor',async()=>{
  vi.mocked(api.get).mockResolvedValue(liste([irsaliye('NONE')]) as never);
  vi.mocked(api.post).mockResolvedValue({data:irsaliye('QUEUED')} as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Gönder'}));
  await waitFor(()=>expect(api.post)
   .toHaveBeenCalledWith('/despatch-notes/7/edespatch/submit'));
  expect(await screen.findByText('e-İrsaliye: kuyrukta')).toBeInTheDocument();

  vi.mocked(api.post).mockResolvedValue({data:irsaliye('SENT')} as never);
  fireEvent.click(screen.getByRole('button',{name:/Durumu Sorgula/}));
  await waitFor(()=>expect(api.post)
   .toHaveBeenCalledWith('/despatch-notes/7/edespatch/sync'));
  expect(await screen.findByText('e-İrsaliye: gönderildi')).toBeInTheDocument();
 });

 it('sunucu hatasini gosteriyor',async()=>{
  vi.mocked(api.get).mockResolvedValue(liste([irsaliye('NONE')]) as never);
  vi.mocked(api.post).mockRejectedValue(new Error('409'));
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Gönder'}));
  expect(await screen.findByText('e-İrsaliye gönderilemedi.')).toBeInTheDocument();
 });
});
