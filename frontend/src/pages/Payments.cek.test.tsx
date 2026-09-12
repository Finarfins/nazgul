/**
 * CS4 — `/odemeler` formunda çek/senet yöntemi.
 *
 * ÖLÇÜLEN KARAR: CS2'den sonra `POST /api/payments` yöntemi çek/senet ise
 * evrak alanlarını zorunlu kılıyor (422). Bu form o yükü kurmaz; yöntem
 * çek/senet iken Kaydet, CS3'ün "Yeni Çek / Senet" penceresini ÖN DOLGULU
 * açar ve evrak `payment_olustur:true` ile yazılır — ödeme satırını sunucu
 * doğurur (ters köprü).
 */
import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import Payments from './Payments';

const get=vi.fn();
const post=vi.fn();
const put=vi.fn();
const del=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:unknown[])=>get(...a),post:(...a:unknown[])=>post(...a),put:(...a:unknown[])=>put(...a),delete:(...a:unknown[])=>del(...a)},
 money:(v:unknown)=>String(v),
}));
vi.mock('../components/ExcelImportDialog',()=>({default:()=>null}));
vi.mock('../components/ResponsiveTable',async()=>{
 const R=await import('react');
 return {default:(props:{rows:Record<string,unknown>[];columns:{field:string;renderCell?:(p:unknown)=>React.ReactNode;valueFormatter?:(v:unknown,r:unknown)=>React.ReactNode}[]})=>
  R.createElement('div',{'data-testid':'tablo'},props.rows.map(row=>R.createElement('div',{key:String(row.id),'data-testid':`satir-${row.id}`},
   props.columns.map(c=>R.createElement('span',{key:c.field},
    c.renderCell?c.renderCell({row,value:row[c.field],field:c.field}):c.valueFormatter?c.valueFormatter(row[c.field],row):String(row[c.field]??'')))))),
 };
});

let izinler:string[]=[];
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(izin:string)=>izinler.includes(izin)}),
}));

let satirlar:Record<string,unknown>[]=[];

beforeEach(()=>{
 izinler=['read','purchases','payments','sales'];
 satirlar=[];
 get.mockReset();post.mockReset();put.mockReset();del.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/payments')return Promise.resolve({data:satirlar});
  if(url==='/payments/summary')return Promise.resolve({data:{customer_total:0,supplier_total:0,manual_total:0,document_total:0,movement_count:satirlar.length}});
  if(url==='/payments/accounts')return Promise.resolve({data:[]});
  if(url==='/customers')return Promise.resolve({data:[{id:1,name:'Ahmet Çiftçi'}]});
  if(url==='/suppliers')return Promise.resolve({data:[{id:7,name:'Gübre A.Ş.'}]});
  return Promise.resolve({data:[]});
 });
 post.mockResolvedValue({data:{}});
});
afterEach(()=>cleanup());

const mount=(entry='/odemeler')=>render(
 <ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[entry]}><Payments/></MemoryRouter></ThemeProvider>);

const sec=(etiket:string,secenek:string)=>{
 fireEvent.mouseDown(screen.getByRole('combobox',{name:etiket}));
 fireEvent.click(within(screen.getByRole('listbox')).getByRole('option',{name:secenek}));
};

/** Formu açıp müşteri + tutar + tarih + not doldurur, yöntemi çek yapar. */
const cekFormu=async()=>{
 mount();
 fireEvent.click(await screen.findByRole('button',{name:'Yeni Hareket'}));
 await screen.findByText('Yeni Tahsilat / Ödeme');
 const cari=screen.getByRole('combobox',{name:'Cari'});
 fireEvent.mouseDown(cari);
 fireEvent.change(cari,{target:{value:'Ahmet'}});
 fireEvent.click(await screen.findByRole('option',{name:'Ahmet Çiftçi'}));
 fireEvent.change(screen.getByLabelText('Tutar'),{target:{value:'1250.50'}});
 fireEvent.change(screen.getByLabelText('Tarih'),{target:{value:'2026-09-20'}});
 fireEvent.change(screen.getByLabelText('Not'),{target:{value:'Eylül tahsilatı'}});
 sec('Ödeme Yöntemi','Çek');
};

describe('CS4 — Payments formunda çek/senet',()=>{
 it('Çek seçip Kaydet: POST /payments ÇAĞRILMAZ, pencere ön dolgulu açılır',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  // Asıl iddia: çıplak ödeme isteği HİÇ atılmadı.
  expect(post).not.toHaveBeenCalledWith('/payments',expect.anything());
  expect(within(pencere).getByRole('combobox',{name:'Evrak Türü'})).toHaveTextContent('Çek');
  expect(within(pencere).getByRole('combobox',{name:'Yön'})).toHaveTextContent('Alınan (müşteriden)');
  expect(within(pencere).getByLabelText('Müşteri')).toHaveValue('Ahmet Çiftçi');
  expect(within(pencere).getByLabelText('Tutar')).toHaveValue(1250.5);
  expect(within(pencere).getByLabelText('Vade')).toHaveValue('2026-09-20');
  expect(within(pencere).getByLabelText('Not')).toHaveValue('Eylül tahsilatı');
 });

 it('Senet seçimi pencereyi senet olarak açar',async()=>{
  await cekFormu();
  sec('Ödeme Yöntemi','Senet');
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  expect(within(pencere).getByRole('combobox',{name:'Evrak Türü'})).toHaveTextContent('Senet');
  expect(post).not.toHaveBeenCalledWith('/payments',expect.anything());
 });

 it('tedarikçi ödemesinde yön VERİLEN gelir',async()=>{
  mount();
  fireEvent.click(await screen.findByRole('button',{name:'Yeni Hareket'}));
  await screen.findByText('Yeni Tahsilat / Ödeme');
  sec('İşlem Türü','Tedarikçi Ödemesi');
  const cari=screen.getByRole('combobox',{name:'Cari'});
  fireEvent.mouseDown(cari);
  fireEvent.change(cari,{target:{value:'Gübre'}});
  fireEvent.click(await screen.findByRole('option',{name:'Gübre A.Ş.'}));
  fireEvent.change(screen.getByLabelText('Tutar'),{target:{value:'500'}});
  sec('Ödeme Yöntemi','Çek');
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  expect(within(pencere).getByRole('combobox',{name:'Yön'})).toHaveTextContent('Verilen (tedarikçiye)');
  expect(within(pencere).getByLabelText('Tedarikçi')).toHaveValue('Gübre A.Ş.');
 });

 it('kaydedilen evrak `payment_olustur:true` gider ve liste yenilenir',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:' A-1 '}});
  const oncekiListe=get.mock.calls.filter(([u])=>u==='/payments').length;
  post.mockResolvedValueOnce({data:{id:99}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler',{
   // `amount` formda SAYI tutulur: '1250.50' -> 1250.5. Değer aynı, metni değil.
   tur:'cek',yon:'alinan',tutar:'1250.5',vade:'2026-09-20',seri_no:'A-1',customer_id:1,
   notlar:'Eylül tahsilatı',payment_olustur:true,odeme_tarihi:'2026-09-20',
  }));
  // Ödeme satırını SUNUCU yazar: istemci `/payments`e hiç dokunmaz.
  expect(post).not.toHaveBeenCalledWith('/payments',expect.anything());
  await waitFor(()=>expect(get.mock.calls.filter(([u])=>u==='/payments').length).toBeGreaterThan(oncekiListe));
 });

 it('Vazgeç hiçbir şey yazmaz',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  fireEvent.click(within(pencere).getByRole('button',{name:'Vazgeç'}));
  await waitFor(()=>expect(screen.queryByRole('dialog',{name:'Yeni Çek / Senet'})).toBeNull());
  expect(post).not.toHaveBeenCalled();
  expect(screen.getByText('Yeni Tahsilat / Ödeme')).toBeInTheDocument();
 });

 it('sunucunun 422 evrak hatası pencerede görünür',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:'A-1'}});
  const metin='Çek/senet ile ödemede evrak bilgisi (cek_senet: vade, seri_no) zorunludur';
  post.mockRejectedValueOnce({response:{status:422,data:{detail:metin}}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  expect(await within(pencere).findByText(metin)).toBeInTheDocument();
 });

 it('`payments` taşımayan rol çek/senet seçeneğini FORMDA görmez',async()=>{
  izinler=['read','sales','purchases'];
  mount();
  fireEvent.click(await screen.findByRole('button',{name:'Yeni Hareket'}));
  await screen.findByText('Yeni Tahsilat / Ödeme');
  fireEvent.mouseDown(screen.getByRole('combobox',{name:'Ödeme Yöntemi'}));
  const liste=within(screen.getByRole('listbox'));
  expect(liste.getByRole('option',{name:'Nakit'})).toBeInTheDocument();
  expect(liste.queryByRole('option',{name:'Çek'})).toBeNull();
  expect(liste.queryByRole('option',{name:'Senet'})).toBeNull();
 });
});

/**
 * CS2'nin PUT/DELETE kilidi (`CEK_BAGLI_ODEME`) ve 422'si bu formda SUNUCU
 * METNİYLE görünür. `api.ts` önleyicisi (#130) kodlu gövdeyi ve doğrulama
 * dizisini `error.message`a çevirip `detail`i o metinle DEĞİŞTİRİR; bu dosyada
 * önleyici yok, o yüzden DÜZELTİLMİŞ gövde taklit edilir.
 */
describe('CS4 — köprülü ödemenin düzenleme/silme yolu',()=>{
 const bagliSatir={id:42,entity_type:'customer',entity_id:1,entity_name:'Ahmet Çiftçi',amount:'1250.50',
  payment_date:'2026-09-20',payment_method:'check',note:'Çek A-1',reference_type:null,is_document_payment:false};
 const KILIT='Bu ödeme bir çek/senet evrakına bağlı; evrak üzerinden yönetilir';

 const dugme=(satir:HTMLElement,ikon:string)=>{
  const el=within(satir).getByTestId(ikon).closest('button');
  if(!el)throw new Error(`${ikon} düğmesi yok`);
  return el;
 };

 it('409 CEK_BAGLI_ODEME düzenlemede sunucu metnini gösterir ve satır KALIR',async()=>{
  satirlar=[bagliSatir];
  mount();
  const satir=await screen.findByTestId('satir-42');
  fireEvent.click(dugme(satir,'EditIcon'));
  await screen.findByText('Hareket Düzenle');
  put.mockRejectedValueOnce({response:{status:409,data:{detail:KILIT}},message:KILIT});
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  // Sayfa şeridi ve pencere aynı metni gösterir; ikisi de sunucunun metni.
  expect((await screen.findAllByText(KILIT)).length).toBeGreaterThan(0);
  await waitFor(()=>expect(put).toHaveBeenCalledWith('/payments/42',expect.objectContaining({payment_method:'check'})));
  expect(screen.getByTestId('satir-42')).toBeInTheDocument();
 });

 it('409 CEK_BAGLI_ODEME silmede sunucu metnini gösterir ve satır KALIR',async()=>{
  satirlar=[bagliSatir];
  mount();
  const satir=await screen.findByTestId('satir-42');
  del.mockRejectedValueOnce({response:{status:409,data:{detail:KILIT}},message:KILIT});
  fireEvent.click(dugme(satir,'DeleteIcon'));
  expect((await screen.findAllByText(KILIT)).length).toBeGreaterThan(0);
  expect(screen.getByTestId('satir-42')).toBeInTheDocument();
 });

 it('422 doğrulama mesajı düzenlemede görünür',async()=>{
  satirlar=[bagliSatir];
  mount();
  const satir=await screen.findByTestId('satir-42');
  fireEvent.click(dugme(satir,'EditIcon'));
  await screen.findByText('Hareket Düzenle');
  const metin='cek_senet yalnız yeni ödemede gönderilebilir';
  put.mockRejectedValueOnce({response:{status:422,data:{detail:metin}},message:metin});
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  expect((await screen.findAllByText(metin)).length).toBeGreaterThan(0);
 });
});
