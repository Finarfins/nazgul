import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import CekSenetPortfoyu from './CekSenetPortfoyu';
import {type CekSenet,type Durum,gunEkle,yerelTarih} from './cekSenet';

const get=vi.fn();
const post=vi.fn();
vi.mock('../../api',()=>({
 api:{get:(...args:unknown[])=>get(...args),post:(...args:unknown[])=>post(...args)},
 money:(value:unknown)=>`₺${value}`,
}));

// İzinler testten TAK EDİLİR (Payments.test.tsx deseni): gerçek AuthProvider
// bir `/api/auth/me` turu ister ve bu dosyanın ölçtüğü şey o değil.
let izinler:string[]=[];
vi.mock('../../AuthContext',()=>({
 useAuth:()=>({can:(izin:string)=>izinler.includes(izin)}),
}));

// DataGrid jsdom'da sütun sanallaştırması yapar; tablo, satır başına sütunların
// `renderCell`/`valueFormatter` çıktısını düz çizen bir ikizle değiştirilir.
vi.mock('../../components/ResponsiveTable',async()=>{
 const R=await import('react');
 return {default:(props:{rows:Record<string,unknown>[];columns:{field:string;renderCell?:(p:unknown)=>React.ReactNode;valueFormatter?:(v:unknown,r:unknown)=>React.ReactNode}[]})=>
  R.createElement('div',{'data-testid':'tablo'},props.rows.map(row=>R.createElement('div',{key:String(row.id),'data-testid':`satir-${row.id}`},
   props.columns.map(c=>R.createElement('span',{key:c.field},
    c.renderCell?c.renderCell({row,value:row[c.field],field:c.field}):c.valueFormatter?c.valueFormatter(row[c.field],row):String(row[c.field]??'')))))),
 };
});

const BUGUN=yerelTarih();

const evrak=(id:number,durum:Durum,ek:Partial<CekSenet>={}):CekSenet=>({
 id,tur:'cek',yon:'alinan',portfoy_durumu:durum,customer_id:1,supplier_id:null,endorsed_supplier_id:null,
 endorsed_date:null,tutar:'1000.00',vade:gunEkle(BUGUN,10),keside_tarihi:null,banka_adi:'Ziraat',sube_adi:null,
 hesap_no:null,seri_no:`S-${id}`,kesideci:null,tahsil_hesap_id:null,tahsil_tarihi:null,payment_id:null,
 financial_transaction_id:null,charge_document_id:null,notlar:null,created_at:'2026-09-10T10:00:00Z',created_by:1,
 ...ek,
});

let liste:{items:CekSenet[];total:number}={items:[],total:0};
let takvim:Record<string,CekSenet[]>={portfoyde:[],tahsile_verildi:[]};
let listeHatasi:unknown=null;

type Params=Record<string,unknown>;
const listeCagrilari=()=>get.mock.calls
 .filter(([url,opt])=>url==='/cek-senetler'&&(opt as {params:Params}).params.limit!==200)
 .map(([,opt])=>(opt as {params:Params}).params);
const sonListeParams=()=>listeCagrilari().at(-1);

beforeEach(()=>{
 izinler=['read','sales','purchases','payments'];
 liste={items:[evrak(11,'portfoyde')],total:1};
 takvim={portfoyde:[],tahsile_verildi:[]};
 listeHatasi=null;
 get.mockReset();post.mockReset();
 get.mockImplementation((url:string,opt?:{params?:Params})=>{
  if(url==='/cek-senetler'){
   const p=opt?.params||{};
   if(p.limit===200){
    const items=takvim[String(p.portfoy_durumu)]||[];
    return Promise.resolve({data:{items,total:items.length,limit:200,offset:0}});
   }
   if(listeHatasi)return Promise.reject(listeHatasi);
   return Promise.resolve({data:{...liste,limit:p.limit,offset:p.offset}});
  }
  if(url==='/customers')return Promise.resolve({data:[{id:1,name:'Ahmet Çiftçi'},{id:2,name:'Mehmet Bey'}]});
  if(url==='/suppliers')return Promise.resolve({data:[{id:7,name:'Gübre A.Ş.'}]});
  if(url==='/payments/accounts')return Promise.resolve({data:[
   {id:55,name:'Merkez Kasa',account_type:'cash',currency:'TRY',is_active:true},
   {id:56,name:'Ziraat Hesap',account_type:'bank',currency:'TRY',is_active:true},
   {id:57,name:'POS Cihazı',account_type:'pos',currency:'TRY',is_active:true},
  ]});
  return Promise.resolve({data:[]});
 });
 post.mockResolvedValue({data:{}});
});
afterEach(()=>cleanup());

function mount(){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={['/cek-senet-portfoyu']}><CekSenetPortfoyu/></MemoryRouter></ThemeProvider>);
}

const sec=(etiket:string,secenek:string)=>{
 fireEvent.mouseDown(screen.getByRole('combobox',{name:etiket}));
 fireEvent.click(within(screen.getByRole('listbox')).getByRole('option',{name:secenek}));
};
const otoSec=async(kapsam:HTMLElement,etiket:string,aranan:string,secenek:string)=>{
 const kutu=within(kapsam).getByRole('combobox',{name:etiket});
 fireEvent.mouseDown(kutu);
 fireEvent.change(kutu,{target:{value:aranan}});
 fireEvent.click(await screen.findByRole('option',{name:secenek}));
};
const menuAc=async(seriNo:string)=>{
 fireEvent.click(await screen.findByRole('button',{name:`${seriNo} — İşlemler`}));
 return screen.findByRole('menu');
};

describe('Çek / Senet Portföyü — liste',()=>{
 it('ilk yüklemede ilk sayfayı ister, satırı ve durum çipini çizer',async()=>{
  mount();
  expect(await screen.findByText('S-11')).toBeInTheDocument();
  expect(sonListeParams()).toEqual({limit:25,offset:0});
  expect(within(screen.getByTestId('satir-11')).getByText('Portföyde')).toBeInTheDocument();
  expect(within(screen.getByTestId('satir-11')).getByText('Ahmet Çiftçi')).toBeInTheDocument();
  expect(screen.getByText('Vade Takvimi')).toBeInTheDocument();
 });

 it('durum çipleri altı durumu kendi etiketiyle ve renk sınıfıyla çizer',async()=>{
  const durumlar:Durum[]=['portfoyde','tahsile_verildi','tahsil_edildi','ciro_edildi','karsiliksiz','iade'];
  liste={items:durumlar.map((d,i)=>evrak(i+1,d)),total:6};
  mount();
  await screen.findByText('S-1');
  const beklenen:[number,string,string][]=[
   [1,'Portföyde','MuiChip-colorPrimary'],[2,'Tahsilde','MuiChip-colorInfo'],[3,'Tahsil Edildi','MuiChip-colorSuccess'],
   [4,'Ciro Edildi','MuiChip-colorSecondary'],[5,'Karşılıksız','MuiChip-colorError'],[6,'İade','MuiChip-colorDefault'],
  ];
  for(const [id,etiket,sinif] of beklenen){
   const cip=within(screen.getByTestId(`satir-${id}`)).getByText(etiket).closest('.MuiChip-root');
   expect([id,cip?.className.includes(sinif)]).toEqual([id,true]);
  }
 });

 it('süzgeçler doğru sorgu parametrelerini gönderir ve ilk sayfaya döner',async()=>{
  mount();
  await screen.findByText('S-11');
  sec('Tür','Senet');
  await waitFor(()=>expect(sonListeParams()).toEqual({limit:25,offset:0,tur:'senet'}));
  sec('Yön','Verilen');
  await waitFor(()=>expect(sonListeParams()).toMatchObject({tur:'senet',yon:'verilen'}));
  fireEvent.click(screen.getByRole('button',{name:'Tahsilde'}));
  await waitFor(()=>expect(sonListeParams()).toMatchObject({portfoy_durumu:'tahsile_verildi'}));
  fireEvent.change(screen.getByLabelText('Vade Başlangıç'),{target:{value:'2026-09-01'}});
  fireEvent.change(screen.getByLabelText('Vade Bitiş'),{target:{value:'2026-09-30'}});
  await waitFor(()=>expect(sonListeParams()).toMatchObject({vade_from:'2026-09-01',vade_to:'2026-09-30'}));
  fireEvent.change(screen.getByLabelText('Ara (seri no, keşideci, banka)'),{target:{value:'  ABC  '}});
  await waitFor(()=>expect(sonListeParams()).toMatchObject({q:'ABC'}));
  await otoSec(document.body,'Cari','Gübre','Gübre A.Ş.');
  await waitFor(()=>expect(sonListeParams()).toEqual({
   limit:25,offset:0,tur:'senet',yon:'verilen',portfoy_durumu:'tahsile_verildi',supplier_id:7,
   vade_from:'2026-09-01',vade_to:'2026-09-30',q:'ABC',
  }));
  fireEvent.click(screen.getByRole('button',{name:'Temizle'}));
  await waitFor(()=>expect(sonListeParams()).toEqual({limit:25,offset:0}));
 });

 it('cari süzgeci müşteri seçilince customer_id gönderir (supplier_id YOK)',async()=>{
  mount();
  await screen.findByText('S-11');
  await otoSec(document.body,'Cari','Ahmet','Ahmet Çiftçi');
  await waitFor(()=>expect(sonListeParams()).toEqual({limit:25,offset:0,customer_id:1}));
 });

 it('sayfalama offseti sayfa boyutuyla ilerler, süzgeç offseti sıfırlar',async()=>{
  liste={items:Array.from({length:25},(_,i)=>evrak(i+1,'portfoyde')),total:60};
  mount();
  await screen.findByText('S-1');
  fireEvent.click(screen.getByRole('button',{name:'Go to next page'}));
  await waitFor(()=>expect(sonListeParams()).toEqual({limit:25,offset:25}));
  fireEvent.click(screen.getByRole('button',{name:'Go to next page'}));
  await waitFor(()=>expect(sonListeParams()).toEqual({limit:25,offset:50}));
  sec('Tür','Çek');
  await waitFor(()=>expect(sonListeParams()).toEqual({limit:25,offset:0,tur:'cek'}));
 });

 it('toplam çubuğu: sayı sunucudan, tutar yalnız bu sayfadan (etiketiyle)',async()=>{
  liste={items:[evrak(1,'portfoyde',{tutar:'1250.55'}),evrak(2,'portfoyde',{tutar:'999.45'})],total:40};
  mount();
  const cubuk=await screen.findByTestId('cek-toplam-cubugu');
  await waitFor(()=>expect(cubuk).toHaveTextContent('40 evrak · Bu sayfadaki 2 evrakın toplamı: ₺2250'));
 });

 it('süzgecin tamamı tek sayfaya sığınca etiket süzgeç toplamıdır',async()=>{
  liste={items:[evrak(1,'portfoyde',{tutar:'10.10'}),evrak(2,'portfoyde',{tutar:'20.20'})],total:2};
  mount();
  await waitFor(()=>expect(screen.getByTestId('cek-toplam-cubugu')).toHaveTextContent('2 evrak · Süzgeç toplamı: ₺30.3'));
 });

 it('boş portföyde boş durum metni çizilir, vade takvimi yine görünür',async()=>{
  liste={items:[],total:0};
  mount();
  expect(await screen.findByText('Seçilen süzgeçlerde çek/senet yok.')).toBeInTheDocument();
  expect(screen.getByText('Vade Takvimi')).toBeInTheDocument();
  expect(screen.queryByTestId('tablo')).toBeNull();
 });

 it('maskeli hesap no OLDUĞU GİBİ çizilir',async()=>{
  liste={items:[evrak(1,'portfoyde',{hesap_no:'****1234'})],total:1};
  mount();
  expect(await within(await screen.findByTestId('satir-1')).findByText('****1234')).toBeInTheDocument();
 });

 it('403 (depo) yetki mesajı gösterir; eylem düğmeleri ve takvim çizilmez',async()=>{
  izinler=['read','stock','purchases'];
  listeHatasi={response:{status:403,data:{detail:'Bu işlem için yetkiniz yok.'}}};
  mount();
  expect(await screen.findByText('Çek/senet portföyünü görüntüleme yetkiniz yok.')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Yeni Evrak'})).toBeNull();
  expect(screen.queryByRole('button',{name:'Bordro Girişi'})).toBeNull();
  expect(screen.queryByText('Vade Takvimi')).toBeNull();
 });

 it('liste hatasında sunucu mesajı gösterilir',async()=>{
  listeHatasi={response:{status:422,data:{detail:'Tarih geçersiz'}}};
  mount();
  expect(await screen.findByText('Tarih geçersiz')).toBeInTheDocument();
 });
});

describe('Vade takvimi',()=>{
 it('açık evrakları ayrık kovalara böler, vadesi geçmişi vurgular; kova tıklanınca tablo süzülür',async()=>{
  takvim={
   portfoyde:[
    evrak(1,'portfoyde',{vade:gunEkle(BUGUN,-3),tutar:'100.00'}),
    evrak(2,'portfoyde',{vade:BUGUN,tutar:'200.00'}),
    evrak(3,'portfoyde',{vade:gunEkle(BUGUN,7),tutar:'300.00',yon:'verilen',customer_id:null,supplier_id:7}),
   ],
   tahsile_verildi:[evrak(4,'tahsile_verildi',{vade:gunEkle(BUGUN,8),tutar:'400.00'})],
  };
  mount();
  const gecikmis=await screen.findByTestId('vade-kova-gecikmis');
  await waitFor(()=>expect(gecikmis).toHaveTextContent('1 evrak'));
  expect(gecikmis).toHaveTextContent('Alınan ₺100');
  expect(screen.getByTestId('vade-kova-bugun')).toHaveTextContent('1 evrak');
  expect(screen.getByTestId('vade-kova-yedi')).toHaveTextContent('Verilen ₺300');
  expect(screen.getByTestId('vade-kova-otuz')).toHaveTextContent('Alınan ₺400');
  expect(screen.getByText('Vadesi geçmiş açık evraklar')).toBeInTheDocument();
  // Takvim açık durumları AYRI AYRI ister: sunucu tek durum süzgeci alır.
  const takvimCagrilari=get.mock.calls.filter(([u,o])=>u==='/cek-senetler'&&(o as {params:Params}).params.limit===200).map(([,o])=>(o as {params:Params}).params);
  expect(takvimCagrilari).toEqual(expect.arrayContaining([
   {portfoy_durumu:'portfoyde',vade_to:gunEkle(BUGUN,30),limit:200,offset:0},
   {portfoy_durumu:'tahsile_verildi',vade_to:gunEkle(BUGUN,30),limit:200,offset:0},
  ]));
  fireEvent.click(within(screen.getByTestId('vade-kova-bugun')).getByRole('button'));
  await waitFor(()=>expect(sonListeParams()).toMatchObject({vade_from:BUGUN,vade_to:BUGUN,offset:0}));
  fireEvent.click(within(gecikmis).getByRole('button'));
  await waitFor(()=>expect(sonListeParams()).toMatchObject({vade_to:gunEkle(BUGUN,-1)}));
  expect(sonListeParams()).not.toHaveProperty('vade_from');
 });
});

describe('Durum geçişi eylemleri',()=>{
 // Durum -> menüde ETKİN eylemler (alınan evrak). Geri kalanlar görünür ama devre dışı.
 const TABLO:[Durum,string[]][]=[
  ['portfoyde',['Tahsile Ver','Ciro Et','İade']],
  ['tahsile_verildi',['Bankadan Tahsil','Karşılıksız','Portföye Geri Al']],
  ['karsiliksiz',['İade']],
  ['tahsil_edildi',[]],
  ['ciro_edildi',[]],
  ['iade',[]],
 ];
 const HEPSI=['Tahsile Ver','Bankadan Tahsil','Ciro Et','Karşılıksız','İade','Portföye Geri Al'];

 it.each(TABLO)('%s durumunda etkin eylemler: %j',async(durum,etkin)=>{
  liste={items:[evrak(1,durum)],total:1};
  mount();
  const menu=await menuAc('S-1');
  for(const etiket of HEPSI){
   const oge=within(menu).getByRole('menuitem',{name:etiket});
   expect([etiket,oge.getAttribute('aria-disabled')==='true']).toEqual([etiket,!etkin.includes(etiket)]);
  }
 });

 it('verilen evrakta Ciro Et HİÇ yok',async()=>{
  liste={items:[evrak(1,'portfoyde',{yon:'verilen',customer_id:null,supplier_id:7})],total:1};
  mount();
  const menu=await menuAc('S-1');
  expect(within(menu).queryByRole('menuitem',{name:'Ciro Et'})).toBeNull();
  expect(within(menu).getByRole('menuitem',{name:'Tahsile Ver'})).not.toHaveAttribute('aria-disabled');
 });

 it('satis (purchases yok): tedarikçi listesi istenmez, Ciro Et devre dışıdır',async()=>{
  izinler=['read','sales','payments'];
  mount();
  const menu=await menuAc('S-11');
  expect(within(menu).getByRole('menuitem',{name:'Ciro Et'})).toHaveAttribute('aria-disabled','true');
  expect(within(menu).getByRole('menuitem',{name:'Tahsile Ver'})).not.toHaveAttribute('aria-disabled');
  expect(get).not.toHaveBeenCalledWith('/suppliers');
 });

 const eylemYap=async(seriNo:string,etiket:string)=>{
  const menu=await menuAc(seriNo);
  fireEvent.click(within(menu).getByRole('menuitem',{name:etiket}));
  return screen.findByRole('dialog');
 };

 it('Tahsile Ver: yalnız hedef (boş not gönderilmez)',async()=>{
  mount();
  const pencere=await eylemYap('S-11','Tahsile Ver');
  fireEvent.click(within(pencere).getByRole('button',{name:'Tahsile Ver'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler/11/durum-degistir',{hedef:'tahsile_verildi'}));
  expect(await screen.findByText('S-11: Tahsilde')).toBeInTheDocument();
 });

 it('Bankadan Tahsil: yalnız kasa/banka hesapları; hesap + tarih + not',async()=>{
  liste={items:[evrak(12,'tahsile_verildi')],total:1};
  mount();
  const pencere=await eylemYap('S-12','Bankadan Tahsil');
  fireEvent.mouseDown(within(pencere).getByRole('combobox',{name:'Tahsil Hesabı (Kasa / Banka)'}));
  const secenekler=within(screen.getByRole('listbox')).getAllByRole('option').map(o=>o.textContent);
  expect(secenekler).toEqual(['Merkez Kasa (Kasa)','Ziraat Hesap (Banka)']);
  fireEvent.click(within(screen.getByRole('listbox')).getByRole('option',{name:'Ziraat Hesap (Banka)'}));
  fireEvent.change(within(pencere).getByLabelText('Tahsil Tarihi'),{target:{value:'2026-09-15'}});
  fireEvent.change(within(pencere).getByLabelText('Not'),{target:{value:' dekont 42 '}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Bankadan Tahsil'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler/12/durum-degistir',
   {hedef:'tahsil_edildi',tahsil_hesap_id:56,tahsil_tarihi:'2026-09-15',not_metni:'dekont 42'}));
 });

 it('Bankadan Tahsil: hesap seçilmeden istek atılmaz',async()=>{
  liste={items:[evrak(12,'tahsile_verildi')],total:1};
  mount();
  const pencere=await eylemYap('S-12','Bankadan Tahsil');
  fireEvent.click(within(pencere).getByRole('button',{name:'Bankadan Tahsil'}));
  expect(await within(pencere).findByText('Tahsil hesabı ve tahsil tarihi zorunludur.')).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
 });

 it('Ciro Et: tedarikçi + ciro tarihi',async()=>{
  mount();
  const pencere=await eylemYap('S-11','Ciro Et');
  await otoSec(pencere,'Ciro Edilen Tedarikçi','Gübre','Gübre A.Ş.');
  fireEvent.change(within(pencere).getByLabelText('Ciro Tarihi'),{target:{value:'2026-09-12'}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Ciro Et'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler/11/durum-degistir',
   {hedef:'ciro_edildi',endorsed_supplier_id:7,endorsed_date:'2026-09-12'}));
 });

 it('Karşılıksız: hedef + not',async()=>{
  liste={items:[evrak(13,'tahsile_verildi')],total:1};
  mount();
  const pencere=await eylemYap('S-13','Karşılıksız');
  fireEvent.change(within(pencere).getByLabelText('Not'),{target:{value:'Banka iade etti'}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Karşılıksız'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler/13/durum-degistir',{hedef:'karsiliksiz',not_metni:'Banka iade etti'}));
 });

 it('İade (karşılıksızdan)',async()=>{
  liste={items:[evrak(14,'karsiliksiz')],total:1};
  mount();
  const pencere=await eylemYap('S-14','İade');
  fireEvent.click(within(pencere).getByRole('button',{name:'İade'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler/14/durum-degistir',{hedef:'iade'}));
 });

 it('Portföye Geri Al (tahsilden)',async()=>{
  liste={items:[evrak(15,'tahsile_verildi')],total:1};
  mount();
  const pencere=await eylemYap('S-15','Portföye Geri Al');
  fireEvent.click(within(pencere).getByRole('button',{name:'Portföye Geri Al'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler/15/durum-degistir',{hedef:'portfoyde'}));
 });

 it('409 (eşzamanlı değişiklik) sunucu mesajını pencerede gösterir, pencere açık kalır',async()=>{
  post.mockRejectedValueOnce({response:{status:409,data:{detail:{code:'CEK_GECIS_GECERSIZ',message:'İzin verilmeyen portföy geçişi: tahsil_edildi -> tahsile_verildi'}}}});
  mount();
  const pencere=await eylemYap('S-11','Tahsile Ver');
  fireEvent.click(within(pencere).getByRole('button',{name:'Tahsile Ver'}));
  expect(await within(pencere).findByText('İzin verilmeyen portföy geçişi: tahsil_edildi -> tahsile_verildi')).toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
 });

 it('422 sunucu mesajını gösterir',async()=>{
  post.mockRejectedValueOnce({response:{status:422,data:{detail:'Tahsil hesabı bulunamadı ya da kasa/banka hesabı değil'}}});
  liste={items:[evrak(12,'tahsile_verildi')],total:1};
  mount();
  const pencere=await eylemYap('S-12','Bankadan Tahsil');
  sec('Tahsil Hesabı (Kasa / Banka)','Merkez Kasa (Kasa)');
  fireEvent.click(within(pencere).getByRole('button',{name:'Bankadan Tahsil'}));
  expect(await within(pencere).findByText('Tahsil hesabı bulunamadı ya da kasa/banka hesabı değil')).toBeInTheDocument();
 });
});

describe('Yeni evrak ve bordro',()=>{
 it('Yeni Evrak: alınan çek tam yükle; boş isteğe bağlı alanlar gönderilmez',async()=>{
  mount();
  await screen.findByText('S-11');
  fireEvent.click(screen.getByRole('button',{name:'Yeni Evrak'}));
  const pencere=await screen.findByRole('dialog');
  await otoSec(pencere,'Müşteri','Ahmet','Ahmet Çiftçi');
  fireEvent.change(within(pencere).getByLabelText('Tutar'),{target:{value:'1250.50'}});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:' A-1 '}});
  fireEvent.change(within(pencere).getByLabelText('Vade'),{target:{value:'2026-10-01'}});
  fireEvent.change(within(pencere).getByLabelText('Banka'),{target:{value:'Ziraat'}});
  fireEvent.change(within(pencere).getByLabelText('Hesap No'),{target:{value:'TR12'}});
  post.mockResolvedValueOnce({data:evrak(99,'portfoyde')});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler',{
   tur:'cek',yon:'alinan',tutar:'1250.50',vade:'2026-10-01',seri_no:'A-1',customer_id:1,banka_adi:'Ziraat',hesap_no:'TR12',
  }));
  expect(await screen.findByText('Evrak portföye alındı.')).toBeInTheDocument();
 });

 it('Yeni Evrak: verilen senet tedarikçiyle gider; müşteri alanı yükte YOK',async()=>{
  mount();
  await screen.findByText('S-11');
  fireEvent.click(screen.getByRole('button',{name:'Yeni Evrak'}));
  const pencere=await screen.findByRole('dialog');
  sec('Evrak Türü','Senet');
  sec('Yön','Verilen (tedarikçiye)');
  await otoSec(pencere,'Tedarikçi','Gübre','Gübre A.Ş.');
  fireEvent.change(within(pencere).getByLabelText('Tutar'),{target:{value:'500'}});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:'SN-9'}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler',{
   tur:'senet',yon:'verilen',tutar:'500',vade:BUGUN,seri_no:'SN-9',supplier_id:7,
  }));
 });

 it('Yeni Evrak: 422 sunucu mesajı pencerede kalır',async()=>{
  post.mockRejectedValueOnce({response:{status:422,data:{detail:'Müşteri bulunamadı'}}});
  mount();
  await screen.findByText('S-11');
  fireEvent.click(screen.getByRole('button',{name:'Yeni Evrak'}));
  const pencere=await screen.findByRole('dialog');
  await otoSec(pencere,'Müşteri','Ahmet','Ahmet Çiftçi');
  fireEvent.change(within(pencere).getByLabelText('Tutar'),{target:{value:'10'}});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:'X'}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  expect(await within(pencere).findByText('Müşteri bulunamadı')).toBeInTheDocument();
 });

 const bordroDoldur=async()=>{
  mount();
  await screen.findByText('S-11');
  fireEvent.click(screen.getByRole('button',{name:'Bordro Girişi'}));
  const pencere=await screen.findByRole('dialog');
  await otoSec(pencere,'Müşteri','Mehmet','Mehmet Bey');
  fireEvent.change(within(pencere).getByLabelText('1. satır seri no'),{target:{value:'B-1'}});
  fireEvent.change(within(pencere).getByLabelText('1. satır tutar'),{target:{value:'100'}});
  fireEvent.change(within(pencere).getByLabelText('1. satır vade'),{target:{value:'2026-10-01'}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Satır Ekle'}));
  fireEvent.change(within(pencere).getByLabelText('2. satır seri no'),{target:{value:'B-2'}});
  fireEvent.change(within(pencere).getByLabelText('2. satır tutar'),{target:{value:'250.75'}});
  fireEvent.change(within(pencere).getByLabelText('2. satır keşideci'),{target:{value:'Ali Veli'}});
  return pencere;
 };

 it('Bordro: bütün satırlar tek istekte, cari her satırda',async()=>{
  const pencere=await bordroDoldur();
  expect(within(pencere).getByText('2 / 200 satır · Toplam ₺350.75')).toBeInTheDocument();
  post.mockResolvedValueOnce({data:{ids:[31,32]}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Bordroyu Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler/bordro',{satirlar:[
   {tur:'cek',yon:'alinan',tutar:'100',vade:'2026-10-01',seri_no:'B-1',customer_id:2},
   // Yeni satır, bir önceki satırın vadesini devralır.
   {tur:'cek',yon:'alinan',tutar:'250.75',vade:'2026-10-01',seri_no:'B-2',customer_id:2,kesideci:'Ali Veli'},
  ]}));
  expect(await screen.findByText('Bordro kaydedildi: 2 evrak portföye alındı.')).toBeInTheDocument();
 });

 it('Bordro reddedilince (hep-ya-hiç) hatalı satır numarası gösterilir ve satır işaretlenir',async()=>{
  const pencere=await bordroDoldur();
  post.mockRejectedValueOnce({response:{status:422,data:{detail:'2. satır: Alınan evrakta müşteri (customer_id) zorunludur'}}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Bordroyu Kaydet'}));
  expect(await within(pencere).findByText(/Bordro kaydedilmedi \(hiçbir satır yazılmadı\)\. 2\. satır:/)).toBeInTheDocument();
  expect(within(pencere).getByTestId('bordro-satir-2')).toHaveClass('Mui-selected');
  expect(within(pencere).getByTestId('bordro-satir-1')).not.toHaveClass('Mui-selected');
 });

 it('Bordro: taraf doğrulaması biçimi (Satır N:) de satırı işaretler',async()=>{
  const pencere=await bordroDoldur();
  post.mockRejectedValueOnce({response:{status:422,data:{detail:'Satır 1: Müşteri bulunamadı'}}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Bordroyu Kaydet'}));
  expect(await within(pencere).findByText(/Satır 1: Müşteri bulunamadı/)).toBeInTheDocument();
  expect(within(pencere).getByTestId('bordro-satir-1')).toHaveClass('Mui-selected');
 });

 it('Bordro: eksik satır istek atılmadan işaretlenir',async()=>{
  const pencere=await bordroDoldur();
  fireEvent.change(within(pencere).getByLabelText('2. satır tutar'),{target:{value:''}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Bordroyu Kaydet'}));
  expect(await within(pencere).findByText('2. satır: seri no, tutar ve vade zorunludur.')).toBeInTheDocument();
  expect(within(pencere).getByTestId('bordro-satir-2')).toHaveClass('Mui-selected');
  expect(post).not.toHaveBeenCalled();
 });
});
