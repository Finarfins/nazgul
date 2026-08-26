import React from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import ResponsiveTable from './ResponsiveTable';

// MUI picks the card layout from matchMedia; jsdom has no implementation, so
// force every media query to match to render the card (mobile/tablet) branch.
function useCardLayout(matches:boolean){
 window.matchMedia=((query:string)=>({
  matches,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),
  addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn(),
 })) as any;
}

const rows=[{id:1,name:'Test Cari',total:120},{id:2,name:'Diğer Cari',total:80}];
const columns=[{field:'name',headerName:'Cari'},{field:'total',headerName:'Toplam'}];
const cardFields=[{label:'Toplam',value:(row:any)=>String(row.total)}];

const onRowClick=vi.fn();
const edit=vi.fn();
const remove=vi.fn();

const cardActions=[
 {label:'Düzenle',color:'primary' as const,onClick:(row:any)=>edit(row.id)},
 {label:'Sil',color:'error' as const,disabled:(row:any)=>row.id===2,onClick:(row:any)=>remove(row.id)},
];

beforeEach(()=>{onRowClick.mockReset();edit.mockReset();remove.mockReset();useCardLayout(true)});
afterEach(()=>cleanup());

function mount(props:any={}){
 return render(<ThemeProvider theme={createTheme()}>
  <ResponsiveTable rows={rows} columns={columns as any} cardTitle={(row:any)=>row.name}
   cardFields={cardFields} onRowClick={onRowClick} {...props}/>
 </ThemeProvider>);
}

it('kart görünümünde her satır için aksiyonları render eder',async()=>{
 mount({cardActions});
 expect(await screen.findByRole('button',{name:'Test Cari — Düzenle'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Test Cari — Sil'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Diğer Cari — Düzenle'})).toBeInTheDocument();
});

it('aksiyon tıklaması satırı açmadan ilgili satırla çalışır',async()=>{
 mount({cardActions});
 fireEvent.click(await screen.findByRole('button',{name:'Test Cari — Düzenle'}));
 expect(edit).toHaveBeenCalledWith(1);
 // stopPropagation: aksiyon satır detayını açmamalı.
 expect(onRowClick).not.toHaveBeenCalled();
});

it('disabled aksiyon tetiklenmez',async()=>{
 mount({cardActions});
 const disabled=await screen.findByRole('button',{name:'Diğer Cari — Sil'});
 expect(disabled).toBeDisabled();
 fireEvent.click(disabled);
 expect(remove).not.toHaveBeenCalled();
});

it.each([true,false])('satıra uymayan koşullu aksiyonu kart ve masaüstünde gizler (cards=%s)',async cards=>{
 useCardLayout(cards);
 mount({
  cardActions:[{
   label:'Yalnız ilk satır',
   hidden:(row:any)=>row.id!==1,
   onClick:(row:any)=>edit(row.id),
  }],
  cardActionsOnDesktop:true,
 });
 expect(await screen.findByRole('button',{name:'Test Cari — Yalnız ilk satır'})).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Diğer Cari — Yalnız ilk satır'})).toBeNull();
});

it.each([true,false])('satır bazlı erişilebilir aksiyon adını kart ve masaüstünde korur (cards=%s)',async cards=>{
 useCardLayout(cards);
 mount({
  cardActions:[{label:'Sil',ariaLabel:(row:any)=>`${row.name} sil`,onClick:(row:any)=>remove(row.id)}],
  cardActionsOnDesktop:true,
 });
 expect(await screen.findByRole('button',{name:'Test Cari sil'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Diğer Cari sil'})).toBeInTheDocument();
});

it('aksiyonlar klavye ile erişilebilir',async()=>{
 mount({cardActions});
 const action=await screen.findByRole('button',{name:'Test Cari — Düzenle'});
 action.focus();
 expect(action).toHaveFocus();
 // Gerçek <button> olduğu için Enter/Space native olarak click üretir.
 expect(action.tagName).toBe('BUTTON');
 expect(action.closest('button')).toBe(action);
});

it('ikonlu aksiyonlar da erişilebilir isim korur',async()=>{
 mount({cardActions:[{label:'Sil',icon:<span data-testid="trash"/>,onClick:(row:any)=>remove(row.id)}]});
 // Her satır kendi ikonlu aksiyonunu alır.
 expect(await screen.findAllByTestId('trash')).toHaveLength(rows.length);
 fireEvent.click(screen.getByRole('button',{name:'Test Cari — Sil'}));
 expect(remove).toHaveBeenCalledWith(1);
});

it('cardActions verilmediğinde mevcut davranış korunur',async()=>{
 mount();
 expect(await screen.findByText('Test Cari')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:/Düzenle/})).toBeNull();
 fireEvent.click(screen.getByText('Test Cari'));
 expect(onRowClick).toHaveBeenCalledWith(rows[0]);
});

// Satır açılabilir DEĞİLKEN kart gövdesi devre dışı `CardActionArea` ile
// sarılıyordu; MUI ona `pointer-events:none` verdiği için kart ALANLARININ
// içindeki bağlantılar ölüyordu — mobil görünüm masaüstünün etkileşim alt
// kümesine düşüyordu. Bu test o regresyonu kilitler.
it('onRowClick yokken kart alanındaki bağlantı devre dışı sarmalayıcıya girmez',async()=>{
 mount({onRowClick:undefined,cardFields:[{label:'Bağlantı',value:()=><a href="/hedef">Detay</a>}]});
 const links=await screen.findAllByRole('link',{name:'Detay'});
 expect(links).toHaveLength(rows.length);
 links.forEach(link=>{
  expect(link.closest('.Mui-disabled')).toBeNull();
  expect(link.closest('.MuiCardActionArea-root')).toBeNull();
 });
});

it('masaüstü tablo cardActions opt-in verilmediğinde mevcut sütunları korur',async()=>{
 useCardLayout(false);
 mount({cardActions});
 expect(screen.queryByRole('button',{name:'Test Cari — Düzenle'})).toBeNull();
});

it('masaüstü tablo görünümünde opt-in cardActions her satır için render edilir',async()=>{
 useCardLayout(false);
 mount({cardActions,cardActionsOnDesktop:true});
 expect(await screen.findByRole('button',{name:'Test Cari — Düzenle'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Test Cari — Sil'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Diğer Cari — Düzenle'})).toBeInTheDocument();
});

it('harici sunucu sayfalaması kullanıldığında DataGrid footerını gizler',async()=>{
 useCardLayout(false);
 mount({hideFooter:true});
 expect(await screen.findByText('Test Cari')).toBeInTheDocument();
 expect(document.querySelector('.MuiDataGrid-footerContainer')).toBeNull();
});

it('masaüstü tablo aksiyonu ilgili satırla çalışır',async()=>{
 useCardLayout(false);
 mount({cardActions,cardActionsOnDesktop:true});
 fireEvent.click(await screen.findByRole('button',{name:'Test Cari — Düzenle'}));
 expect(edit).toHaveBeenCalledWith(1);
 expect(onRowClick).not.toHaveBeenCalled();
});

it('masaüstü tabloda disabled cardAction tetiklenmez',async()=>{
 useCardLayout(false);
 mount({cardActions,cardActionsOnDesktop:true});
 const disabled=await screen.findByRole('button',{name:'Diğer Cari — Sil'});
 expect(disabled).toBeDisabled();
 fireEvent.click(disabled);
 expect(remove).not.toHaveBeenCalled();
});

it('boş mobil listede açıklayıcı durum gösterir',()=>{
 mount({rows:[]});
 expect(screen.getByText('Henüz kayıt yok')).toBeInTheDocument();
 expect(screen.getByText(/Yeni bir kayıt oluşturduğunuzda/)).toBeInTheDocument();
});

// Windows'ta çift tıklamak yaygın alışkanlık. İki tık iki kez gezinirse
// geçmişe aynı adres iki kez girer ve kullanıcı "geri"ye bastığında aynı
// sayfada kalır — geri tuşu bozulmuş görünür.
it('çift tıkta ikinci tık yutulur, tek gezinme olur',async()=>{
 useCardLayout(false);
 mount();
 const cell=await screen.findByText('Test Cari');
 fireEvent.click(cell);
 fireEvent.click(cell);
 expect(onRowClick).toHaveBeenCalledTimes(1);
});

// Masaüstü tablo eskiden onRowDoubleClick kullanıyordu: satır cursor:pointer
// gösterip tek tıka cevap vermiyordu, yani arayüz tek tık vaat edip çift tık
// istiyordu. Mobil kart hep tek tıktı, iki yüzey birbirini tutmuyordu.
it('masaüstü tabloda satır TEK tıkla açılır',async()=>{
 useCardLayout(false);
 mount();
 const cell=await screen.findByText('Test Cari');
 fireEvent.click(cell);
 expect(onRowClick).toHaveBeenCalledTimes(1);
 expect(onRowClick.mock.calls[0][0].id).toBe(1);
});

// KIRPILAN İÇERİK KAYDIRILAMAZ. Kart `overflow:'hidden'` taşıyor: sığmayan
// metin ne kaydırılabilir ne de görülebilir, sadece YOK olur. Boşluklu ad
// kendiliğinden alt satıra geçtiği için kusuru göstermez; kusuru belirleyen
// girdi adın BOŞLUKSUZ olmasıdır. 390px'te ölçüldü: kural kaldırıldığında
// +137px kırpılıyor.
//
// Bu kapı e2e'nin ucuz ikizidir: ResponsiveTable'ı düzenleyen biri kuralı
// düşürürse tarayıcı kapısını beklemeden burada kırmızı görür.
it('boşluksuz uzun ad kırpılmak yerine KIRILIR',async()=>{
 useCardLayout(true);
 const uzunAd='MobilDilimMüşterisiUnvanıSanayiVeTicaretLimitedŞirketi17550000000002';
 mount({rows:[{id:1,name:uzunAd,total:120}]});
 const baslik=await screen.findByText(uzunAd);
 expect(getComputedStyle(baslik).overflowWrap).toBe('anywhere');
});

// Kırma kuralı TEK BAŞINA yetmez: esnek satırda flex çocuğunun öntanımlı
// `min-width:auto` değeri onu içeriğinden dar olmaya bırakmaz ve satır yine
// taşar. İki kural birlikte çivilenmezse biri düşerken diğeri yeşil kalır.
it('kart alanları esnek satırda daralabilir (minWidth:0)',async()=>{
 useCardLayout(true);
 mount({rows:[{id:1,name:'Test Cari',total:120}]});
 const deger=await screen.findByText('120');
 expect(getComputedStyle(deger).minWidth).toBe('0px');
});
