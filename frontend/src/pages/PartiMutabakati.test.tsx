import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import PartiMutabakati from './PartiMutabakati';

// Rapor yalnız sunucunun söylediğini gösterir. Testler bu sözleşmeyi çiviler:
// kartlar tenant geneli `counts`tan okunur (sayfadan sayılmaz), fark işareti
// renge bağlanır, SAPMA sayfa içinde önce gelir, sayfalama offset biriktirir,
// ürün/depo adları TEK liste isteğiyle çözülür.
const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 errorDetail:(e:any,fallback:string)=>e?.response?.data?.detail||fallback,
}));

const theme=createTheme();
const PRODUCTS=[
 {id:1,name:'Motor Yağı',product_code:'MY-1'},
 {id:2,name:'Tohum',product_code:'TH-2'},
 {id:3,name:'Gübre',product_code:null},
];
const WAREHOUSES=[{id:10,name:'Merkez Depo'},{id:11,name:'Şube Depo'}];

const satir=(product_id:number,warehouse_id:number,stok:string,parti_toplami:string,fark:string,kova:string)=>
 ({product_id,warehouse_id,stok,parti_toplami,fark,kova});

// Sunucu sırası ürün/depo kimliğidir; kova sırası bilinçli karışık.
const SAYFA1={
 items:[
  satir(1,10,'5.0000','5.0000','0.0000','ESIT'),
  satir(2,10,'8.0000','0.0000','8.0000','LOTSUZ_TASARIM'),
  satir(3,11,'2.0000','7.5000','-5.5000','SAPMA'),
 ],
 // Tenant geneli: sayfadaki 1/1/1 DEĞİL.
 counts:{ESIT:40,LOTSUZ_TASARIM:150,SAPMA:17},
 total:207,has_more:true,bos_ciftler:12,
};
const SAYFA2={
 items:[satir(99,77,'1.0000','0.0000','1.0000','LOTSUZ_TASARIM')],
 counts:{ESIT:40,LOTSUZ_TASARIM:150,SAPMA:17},
 total:207,has_more:false,bos_ciftler:12,
};

let raporCevaplari:Record<number,any>;
function route(url:string,config?:any){
 if(url==='/products')return {data:PRODUCTS};
 if(url==='/warehouses')return {data:WAREHOUSES};
 if(url==='/products/lots/mutabakat'){
  const offset=config?.params?.offset??0;
  if(!(offset in raporCevaplari))throw new Error(`beklenmeyen offset: ${offset}`);
  return {data:raporCevaplari[offset]};
 }
 throw new Error(`beklenmeyen istek: ${url}`);
}

beforeEach(()=>{
 navigate.mockReset();get.mockReset();
 raporCevaplari={0:SAYFA1,200:SAYFA2};
 get.mockImplementation((url:string,config?:any)=>Promise.resolve(route(url,config)));
});
afterEach(()=>cleanup());

function mount(){
 return render(<ThemeProvider theme={theme}><MemoryRouter><PartiMutabakati/></MemoryRouter></ThemeProvider>);
}
const cagrilar=(url:string)=>get.mock.calls.filter(([u])=>u===url);
const renk=(el:Element)=>getComputedStyle(el).color;
// Tema rengini jsdom'un hesapladığı biçime (rgb) çevirmek için aynı yoldan geçir.
function beklenenRenk(color:string){
 const probe=document.createElement('span');
 probe.style.color=color;
 document.body.appendChild(probe);
 const value=getComputedStyle(probe).color;
 probe.remove();
 return value;
}

it('kova kartlarını sayfa satırlarından değil tenant geneli counts\'tan gösterir',async()=>{
 mount();
 await screen.findByText('207 çift');
 expect(within(screen.getByTestId('kova-SAPMA')).getByText('17')).toBeTruthy();
 expect(within(screen.getByTestId('kova-LOTSUZ_TASARIM')).getByText('150')).toBeTruthy();
 expect(within(screen.getByTestId('kova-ESIT')).getByText('40')).toBeTruthy();
 expect(screen.getByTestId('bos-ciftler').textContent).toMatch(/^12 rapordan düşülen boş çift/);
 // Açıklama docstring'den; sayfa satırı sayısı (1) hiçbir kartta yok.
 expect(within(screen.getByTestId('kova-SAPMA')).getByText(/incelenmesi gereken tek kova/)).toBeTruthy();
 expect(within(screen.getByTestId('kova-SAPMA')).queryByText('1')).toBeNull();
});

it('negatif fark hata, pozitif fark uyarı rengi alır; sıfır fark renksiz kalır',async()=>{
 mount();
 await screen.findByText('207 çift');
 const negatif=within(screen.getByTestId('satir-3-11')).getByTestId('fark');
 const pozitif=within(screen.getByTestId('satir-2-10')).getByTestId('fark');
 const sifir=within(screen.getByTestId('satir-1-10')).getByTestId('fark');
 expect(negatif.textContent).toBe('-5.5');
 expect(pozitif.textContent).toBe('+8');
 expect(sifir.textContent).toBe('0');
 expect(renk(negatif)).toBe(beklenenRenk(theme.palette.error.main));
 expect(renk(pozitif)).toBe(beklenenRenk(theme.palette.warning.main));
 expect(renk(sifir)).not.toBe(beklenenRenk(theme.palette.error.main));
 expect(renk(sifir)).not.toBe(beklenenRenk(theme.palette.warning.main));
});

it('sayfa içinde SAPMA önce, sonra LOTSUZ_TASARIM, sonra ESIT sıralanır ve bunu söyler',async()=>{
 mount();
 await screen.findByText('207 çift');
 const sira=screen.getAllByTestId(/^satir-/).map(el=>el.getAttribute('data-testid'));
 expect(sira).toEqual(['satir-3-11','satir-2-10','satir-1-10']);
 expect(screen.getByText(/Kova filtresi yok/)).toBeTruthy();
 expect(screen.queryByRole('combobox')).toBeNull();
});

// Sunucu sözleşmesi: has_more true ise sayfa TAM limit (200) satırdır.
const doluSayfa=(sayfa:typeof SAYFA1)=>({...sayfa,items:[
 ...sayfa.items,
 ...Array.from({length:200-sayfa.items.length},(_,i)=>satir(1000+i,10,'1.0000','1.0000','0.0000','ESIT')),
]});

it('"Daha fazla" offset 200 ile sonraki sayfayı ekler ve has_more false olunca kaybolur',async()=>{
 raporCevaplari={0:doluSayfa(SAYFA1),200:SAYFA2};
 mount();
 await screen.findByText('207 çift');
 expect(cagrilar('/products/lots/mutabakat')[0][1]).toEqual({params:{limit:200,offset:0}});
 fireEvent.click(screen.getByRole('button',{name:'Daha fazla'}));
 await screen.findByTestId('satir-99-77');
 expect(cagrilar('/products/lots/mutabakat')[1][1]).toEqual({params:{limit:200,offset:200}});
 expect(screen.getAllByTestId(/^satir-/)).toHaveLength(201);
 expect(screen.queryByRole('button',{name:'Daha fazla'})).toBeNull();
});

it('ürün ve depo adlarını listelerden çözer; her liste bir kez istenir, satır tıklaması ürün kartına gider',async()=>{
 raporCevaplari={0:doluSayfa(SAYFA1),200:SAYFA2};
 mount();
 await screen.findByText('207 çift');
 await waitFor(()=>expect(screen.getByText('Motor Yağı')).toBeTruthy());
 const sapma=within(screen.getByTestId('satir-3-11'));
 expect(sapma.getByText('Gübre')).toBeTruthy();
 expect(sapma.getByText('Şube Depo')).toBeTruthy();
 expect(within(screen.getByTestId('satir-1-10')).getByText('MY-1')).toBeTruthy();

 fireEvent.click(screen.getByRole('button',{name:'Daha fazla'}));
 await screen.findByTestId('satir-99-77');
 // Listede olmayan kimlik uydurulmaz, `#id` ile geçer.
 expect(within(screen.getByTestId('satir-99-77')).getByText('Ürün #99')).toBeTruthy();
 expect(within(screen.getByTestId('satir-99-77')).getByText('Depo #77')).toBeTruthy();
 // Sayfa başına değil, ekran başına BİR istek.
 expect(cagrilar('/products')).toHaveLength(1);
 expect(cagrilar('/warehouses')).toHaveLength(1);

 fireEvent.click(screen.getByTestId('satir-3-11'));
 expect(navigate).toHaveBeenCalledWith('/urunler/3');
});

it('boş durum: hiç çift yoksa "rapor boş", hepsi eşitse "ayrışmıyor" der',async()=>{
 raporCevaplari={0:{items:[],counts:{ESIT:0,LOTSUZ_TASARIM:0,SAPMA:0},total:0,has_more:false,bos_ciftler:4}};
 mount();
 expect(await screen.findByText(/Rapor boş/)).toBeTruthy();
 expect(screen.queryByText(/hiçbir çiftte ayrışmıyor/)).toBeNull();
 expect(screen.queryByRole('table')).toBeNull();
 expect(screen.getByTestId('bos-ciftler').textContent).toMatch(/^4 rapordan düşülen boş çift/);
 cleanup();

 raporCevaplari={0:{items:[satir(1,10,'5.0000','5.0000','0.0000','ESIT')],counts:{ESIT:1,LOTSUZ_TASARIM:0,SAPMA:0},total:1,has_more:false,bos_ciftler:0}};
 mount();
 expect(await screen.findByText(/hiçbir çiftte ayrışmıyor/)).toBeTruthy();
 expect(screen.queryByText(/Rapor boş/)).toBeNull();
});

it('500 hatasında sunucunun errorDetail mesajını gösterir',async()=>{
 get.mockImplementation((url:string,config?:any)=>url==='/products/lots/mutabakat'
  ?Promise.reject({response:{status:500,data:{detail:'Mutabakat sorgusu çöktü'}}})
  :Promise.resolve(route(url,config)));
 mount();
 expect(await screen.findByText('Mutabakat sorgusu çöktü')).toBeTruthy();
 expect(screen.queryByTestId('kova-SAPMA')).toBeNull();
});
