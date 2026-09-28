import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import AppShell from '../components/AppShell';
import {NAV_LABELS,permissionForPath} from '../navigation';

import MuhasebeAktarimi,{FIS_SAYFA_BOYU,HEDEFLER,oncekiDonem,tutar} from './MuhasebeAktarimi';

// Ekran yalnız sunucunun söylediğini gösterir. Testler bu sözleşmeyi çiviler:
// KDV özeti satırları ve toplamları olduğu gibi, uyarı `mesaj`ı kelimesi
// kelimesine, `kdv_dahil` ayrı bir rozetle; indirme 409'u Blob gövdeden bir
// HATA LİSTESİ olarak (tost değil); indirme hedef başına doğru target/format.
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:vi.fn()},
 errorDetail:(e:any,fallback:string)=>typeof e?.response?.data?.detail==='string'?e.response.data.detail:fallback,
}));

// Menü görünürlüğü AppShell'le ölçülür: izin `can()`den okunur.
let permissions:string[]=['*'];
vi.mock('../AuthContext',()=>({
 useAuth:()=>({
  user:{id:1,username:'test',display_name:'Test Kullanıcı',role:'admin',must_change_password:false},
  companies:[],activeCompany:null,setActiveCompany:vi.fn(),logout:vi.fn(),
  can:(permission:string)=>permission==='platform'?false:permissions.includes('*')||permissions.includes(permission),
 }),
}));
vi.mock('../ThemeContext',()=>({useAppTheme:()=>({mode:'light',toggle:vi.fn()})}));

const theme=createTheme();
const DONEM='2026-08';

const UYARI_DENGESIZ={
 kod:'DENGESIZ',kaynak:'SATIS',belge_id:null,belge_no:'S-0042',fark:'0.01',
 mesaj:'F-2026-08-0042 fişi yazılmadı; KDV özetine DAHİL: borç ≠ alacak',kdv_dahil:true,
};
const UYARI_G2={kod:'G2',kaynak:'ALIS',belge_id:null,belge_no:null,fark:null,
 mesaj:'%18.00 oranı bugünkü yasal oranlardan (1/10/20) değil; ayrı satırda gösterildi (1 belge)',kdv_dahil:null};

const OZET={
 period:DONEM,
 satirlar:[
  {yon:'HESAPLANAN',tur:'SATIS',oran:'20.00',bilinen_oran:true,matrah:'10000.00',kdv:'2000.00',belge_sayisi:3},
  {yon:'HESAPLANAN',tur:'SERVIS_FATURA',oran:'20.00',bilinen_oran:true,matrah:'500.00',kdv:'100.00',belge_sayisi:1},
  {yon:'INDIRILECEK',tur:'ALIS',oran:'18.00',bilinen_oran:false,matrah:'1000.00',kdv:'180.00',belge_sayisi:1},
  {yon:'INDIRILECEK',tur:'SATIS_IADE',oran:'10.00',bilinen_oran:true,matrah:'200.00',kdv:'20.00',belge_sayisi:1},
 ],
 hesaplanan_kdv:'2100.00',indirilecek_kdv:'200.00',fark:'1900.00',
 mustahsil:{belge_sayisi:2,brut:'3000.00',stopaj:'60.00',bagkur:'30.00',net:'2910.00'},
 uyarilar:[UYARI_G2,UYARI_DENGESIZ],
 kapsam_notlari:['Tevkifat desteklenmiyor: kısmi/tam tevkifat hiçbir belgede saklanmıyor (K9, G1).'],
};

const fis=(no:number)=>({
 fis_no:`F-${String(no).padStart(4,'0')}`,fis_tarihi:'2026-08-05',belge_tipi:'SATIS',belge_no:`S-${no}`,
 belge_tarihi:'2026-08-05',odeme_yontemi:'CARI',kaynak:'SATIS',borc_toplami:'120.00',alacak_toplami:'120.00',
 satirlar:[
  {hesap_kodu:'120.01',borc:'120.00',alacak:'0.00',aciklama:'Cari'},
  {hesap_kodu:'600.01',borc:'0.00',alacak:'100.00',aciklama:'Satış',kdv_orani:'20.00'},
  {hesap_kodu:'391.20',borc:'0.00',alacak:'20.00',aciklama:'KDV',kdv_orani:'20.00'},
 ],
 cari:{ad:'Ahmet Çiftçi',tax_number:'1234567890'},
});
const fisSayfasi=(offset:number,total:number)=>({
 period:DONEM,total,limit:FIS_SAYFA_BOYU,offset,
 items:Array.from({length:Math.min(FIS_SAYFA_BOYU,total-offset)},(_,i)=>fis(offset+i+1)),
 dengesiz_sayisi:1,uyarilar:[],
});

let fisToplami=3;
function route(url:string,config?:any){
 if(url==='/accounting/vat-summary')return {data:{...OZET,period:config.params.period}};
 if(url==='/accounting/vouchers')return {data:fisSayfasi(config.params.offset,fisToplami)};
 // AppShell'in kendi istekleri (bildirim sayacı vb.)
 return {data:{items:[]}};
}

beforeEach(()=>{
 get.mockReset();fisToplami=3;permissions=['*'];
 get.mockImplementation((url:string,config?:any)=>Promise.resolve(route(url,config)));
});
afterEach(()=>{cleanup();vi.restoreAllMocks()});

function mount(){
 return render(<ThemeProvider theme={theme}><MemoryRouter><MuhasebeAktarimi/></MemoryRouter></ThemeProvider>);
}
const cagrilar=(url:string)=>get.mock.calls.filter(([u])=>u===url);
async function donemSec(donem:string){
 fireEvent.change(screen.getByTestId('donem'),{target:{value:donem}});
 await waitFor(()=>expect(cagrilar('/accounting/vat-summary').at(-1)?.[1]).toEqual({params:{period:donem}}));
}

describe('dönem',()=>{
 it('varsayılan dönem Europe/Istanbul\'daki bir önceki aydır',()=>{
  // 31 Ağu 21:30 UTC = 1 Eylül 00:30 İstanbul → önceki ay AĞUSTOS (UTC'de Temmuz olurdu).
  expect(oncekiDonem(new Date('2026-08-31T21:30:00Z'))).toBe('2026-08');
  expect(oncekiDonem(new Date('2026-08-31T20:30:00Z'))).toBe('2026-07');
  expect(oncekiDonem(new Date('2026-01-15T10:00:00Z'))).toBe('2025-12');
 });

 it('seçilen dönem için özeti ve fişleri ister',async()=>{
  mount();
  expect((screen.getByTestId('donem') as HTMLInputElement).value).toBe(oncekiDonem());
  await donemSec(DONEM);
  await screen.findByText(`KDV özeti — ${DONEM}`);
  expect(cagrilar('/accounting/vouchers').at(-1)?.[1]).toEqual({params:{period:DONEM,limit:100,offset:0}});
 });
});

describe('KDV özeti',()=>{
 it('oran satırlarını, toplamları ve müstahsil bölümünü sunucunun değerleriyle çizer',async()=>{
  mount();
  await donemSec(DONEM);
  await screen.findByText(`KDV özeti — ${DONEM}`);
  const satis=within(screen.getByTestId('kdv-HESAPLANAN-SATIS-20.00'));
  expect(satis.getByText('Hesaplanan')).toBeTruthy();
  expect(satis.getByText('Satış')).toBeTruthy();
  expect(satis.getByText('%20')).toBeTruthy();
  expect(satis.getByText('10.000,00')).toBeTruthy();
  expect(satis.getByText('2.000,00')).toBeTruthy();
  const iade=within(screen.getByTestId('kdv-INDIRILECEK-SATIS_IADE-10.00'));
  expect(iade.getByText('İndirilecek')).toBeTruthy();
  expect(iade.getByText('Satış iadesi')).toBeTruthy();
  // Bilinmeyen oran kendi satırında, yasal olmadığı söylenerek.
  expect(within(screen.getByTestId('kdv-INDIRILECEK-ALIS-18.00')).getByText('yasal oran değil')).toBeTruthy();
  expect(within(screen.getByTestId('kdv-HESAPLANAN-SATIS-20.00')).queryByText('yasal oran değil')).toBeNull();
  expect(within(screen.getByTestId('toplam-hesaplanan')).getByText('2.100,00')).toBeTruthy();
  expect(within(screen.getByTestId('toplam-indirilecek')).getByText('200,00')).toBeTruthy();
  expect(within(screen.getByTestId('toplam-fark')).getByText('1.900,00')).toBeTruthy();
  const mustahsil=within(screen.getByTestId('mustahsil'));
  expect(mustahsil.getByText('3.000,00')).toBeTruthy();
  expect(mustahsil.getByText('2.910,00')).toBeTruthy();
  expect(screen.getByText(OZET.kapsam_notlari[0])).toBeTruthy();
 });

 it('tutar biçimlendirici Number()dan geçmez ve eksiyi korur',()=>{
  expect(tutar('12345678901234567.89')).toBe('12.345.678.901.234.567,89');
  expect(tutar('-1900.5')).toBe('-1.900,50');
 });
});

describe('uyarılar',()=>{
 it('mesajı olduğu gibi gösterir; kdv_dahil yalnız taşıyan uyarıda rozet olur',async()=>{
  mount();
  await donemSec(DONEM);
  await screen.findByText(`KDV özeti — ${DONEM}`);
  const uyarilar=screen.getAllByTestId('muhasebe-uyarisi');
  expect(uyarilar).toHaveLength(2);
  const [g2,dengesiz]=uyarilar.map(u=>within(u));
  expect(dengesiz.getByText(UYARI_DENGESIZ.mesaj)).toBeTruthy();
  expect(dengesiz.getByText('DENGESIZ')).toBeTruthy();
  expect(dengesiz.getByTestId('kdv-dahil').textContent).toBe('KDV özetine dahil');
  expect(g2.getByText(UYARI_G2.mesaj)).toBeTruthy();
  expect(g2.queryByTestId('kdv-dahil')).toBeNull();
 });
});

describe('fiş listesi',()=>{
 it('100\'lük sayfalar: Sonraki offset 100 ister, satırlar açılınca fiş satırları görünür',async()=>{
  fisToplami=150;
  mount();
  await donemSec(DONEM);
  expect((await screen.findByTestId('fis-araligi')).textContent).toBe('1–100 / 150 fiş');
  expect(screen.getByText('1 dengesiz fiş listede yok')).toBeTruthy();
  // 100 satırlık DOM'da getByRole yavaştır (erişilebilirlik ağacı her satır
  // için hesaplanır); düğmeler aria-label / metinle bulunur.
  fireEvent.click(within(screen.getByTestId('fis-F-0001')).getByLabelText('Satırları göster'));
  expect(await screen.findByText('391.20')).toBeTruthy();
  fireEvent.click(screen.getByText('Sonraki'));
  await waitFor(()=>expect(screen.getByTestId('fis-araligi').textContent).toBe('101–150 / 150 fiş'));
  expect(cagrilar('/accounting/vouchers').at(-1)?.[1]).toEqual({params:{period:DONEM,limit:100,offset:100}});
  expect((screen.getByText('Sonraki').closest('button') as HTMLButtonElement).disabled).toBe(true);
 });
});

describe('indirme',()=>{
 function blobOrtami(){
  const createObjectURL=vi.fn(()=>'blob:muhasebe');
  Object.assign(URL,{createObjectURL,revokeObjectURL:vi.fn()});
  const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  return {createObjectURL,click};
 }

 it('hedef başına sunucunun desteklediği target/format ile indirir; logo yok',async()=>{
  expect(HEDEFLER.map(h=>h.target)).toEqual(['luca','mikro','canonical']);
  const {createObjectURL,click}=blobOrtami();
  const zip=new Blob(['PK'],{type:'application/zip'});
  get.mockImplementation((url:string,config?:any)=>url==='/accounting/export'
   ?Promise.resolve({data:zip,headers:{'content-disposition':`attachment; filename="muhasebe-${DONEM}-luca.zip"`}})
   :Promise.resolve(route(url,config)));
  mount();
  await donemSec(DONEM);
  expect(screen.queryByRole('button',{name:/logo/i})).toBeNull();

  fireEvent.click(screen.getByRole('button',{name:'Luca (xlsx)'}));
  await waitFor(()=>expect(click).toHaveBeenCalledTimes(1));
  const [url,config]=cagrilar('/accounting/export')[0];
  expect(url).toBe('/accounting/export');
  expect(config.params).toEqual({period:DONEM,target:'luca',format:'xlsx'});
  expect(config.responseType).toBe('blob');
  expect(createObjectURL).toHaveBeenCalledWith(zip);
  expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe(`muhasebe-${DONEM}-luca.zip`);

  await waitFor(()=>expect((screen.getByRole('button',{name:'Kanonik (csv + json)'}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button',{name:'Kanonik (csv + json)'}));
  await waitFor(()=>expect(cagrilar('/accounting/export')).toHaveLength(2));
  expect(cagrilar('/accounting/export')[1][1].params).toEqual({period:DONEM,target:'canonical',format:'csv'});
 });

 it('409 DONEM_DENGESIZ Blob gövdesini hata listesi olarak gösterir',async()=>{
  const {click}=blobOrtami();
  const govde={detail:{
   code:'DONEM_DENGESIZ',
   message:`${DONEM} döneminde 7 dengesiz fiş var; önce belgeleri düzeltin (fiş önizlemesindeki uyarılar).`,
   dengesiz_sayisi:7,
   ilk_hatalar:['F-0003: borç 120.00 ≠ alacak 119.99','F-0009: hesap kodu yok'],
  }};
  get.mockImplementation((url:string,config?:any)=>url==='/accounting/export'
   ?Promise.reject({response:{status:409,data:new Blob([JSON.stringify(govde)],{type:'application/json'})}})
   :Promise.resolve(route(url,config)));
  mount();
  await donemSec(DONEM);
  fireEvent.click(screen.getByRole('button',{name:'Mikro (csv)'}));
  const hata=within(await screen.findByTestId('aktarim-hatasi'));
  expect(hata.getByText('Dönemde dengesiz fiş var')).toBeTruthy();
  expect(hata.getByText(govde.detail.message)).toBeTruthy();
  expect(hata.getAllByRole('listitem').map(li=>li.textContent)).toEqual(govde.detail.ilk_hatalar);
  expect(hata.getByText('7 dengesiz fişin ilk 2 tanesi gösteriliyor.')).toBeTruthy();
  expect(click).not.toHaveBeenCalled();
 });

 it('409 FIS_NO_TEKRARLI başlığıyla ve sunucu mesajıyla görünür',async()=>{
  blobOrtami();
  const govde={detail:{code:'FIS_NO_TEKRARLI',message:'F-0005 fiş no 2026-08-05 tarihinde iki belgeye verildi'}};
  get.mockImplementation((url:string,config?:any)=>url==='/accounting/export'
   ?Promise.reject({response:{status:409,data:new Blob([JSON.stringify(govde)])}})
   :Promise.resolve(route(url,config)));
  mount();
  await donemSec(DONEM);
  fireEvent.click(screen.getByRole('button',{name:'Luca (xlsx)'}));
  const hata=within(await screen.findByTestId('aktarim-hatasi'));
  expect(hata.getByText('Tekrarlı fiş no')).toBeTruthy();
  expect(hata.getByText(govde.detail.message)).toBeTruthy();
 });
});

describe('menü',()=>{
 const ROLLER:Record<string,string[]>={
  rapor:['read','reports'],
  satis:['read','sales','payments'],
  depo:['read','stock','purchases'],
 };
 function kabuk(rol:string){
  permissions=ROLLER[rol];
  return render(<ThemeProvider theme={theme}><MemoryRouter initialEntries={['/']}>
   <Routes><Route element={<AppShell/>}><Route path="*" element={<div>içerik</div>}/></Route></Routes>
  </MemoryRouter></ThemeProvider>);
 }
 const sidebar=()=>document.querySelector('.MuiDrawer-paper') as HTMLElement;
 const finansiAc=()=>{
  const toggle=sidebar().querySelector('[data-nav-toggle="finance"]') as HTMLElement|null;
  if(toggle)fireEvent.click(toggle);
 };

 it('rota `reports` ister; reports taşıyan rol maddeyi görür',async()=>{
  expect(permissionForPath('/raporlar/muhasebe-aktarimi')).toBe('reports');
  kabuk('rapor');
  await waitFor(()=>expect(within(sidebar()).getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  finansiAc();
  expect(await within(sidebar()).findByRole('link',{name:NAV_LABELS.accountingExport})).toBeTruthy();
 });

 it.each(['satis','depo'])('reports taşımayan %s rolü maddeyi görmez',async rol=>{
  kabuk(rol);
  await waitFor(()=>expect(within(sidebar()).getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  finansiAc();
  // satis Finans'ı (Tahsilat) görür ama bu maddeyi GÖRMEZ; depo Finans'ı hiç görmez.
  if(rol==='satis')expect(await within(sidebar()).findByRole('link',{name:NAV_LABELS.payments})).toBeTruthy();
  expect(within(sidebar()).queryByText(NAV_LABELS.accountingExport)).toBeNull();
 });
});
