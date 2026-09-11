import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter,useLocation} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Finance from './Finance';
import financeSource from './Finance.tsx?raw';

/** Router'ın o anki yolu — gezinmenin gerçekten gerçekleştiğini ölçer. */
function Konum(){const {pathname}=useLocation();return <span data-testid="konum">{pathname}</span>}

const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:vi.fn(),put:vi.fn(),delete:vi.fn()},
 money:(value:any)=>String(value),
}));
const tableProps=vi.fn();
vi.mock('../components/ResponsiveTable',()=>({default:(props:any)=>{tableProps(props);return null}}));

const accounts=[{id:55,name:'Merkez Kasa',account_type:'cash',balance:1500,transaction_count:2,is_active:true}];
const txns=[
 {id:79,account_id:55,account_name:'Merkez Kasa',txn_date:'2026-07-12',direction:'in',amount:20,description:'Kasa Hareketi'},
 {id:80,account_id:55,account_name:'Merkez Kasa',txn_date:'2026-07-11',direction:'out',amount:5,description:'Diğer Kasa Hareketi'},
 {id:81,account_id:66,account_name:'Banka',txn_date:'2026-07-10',direction:'in',amount:9,description:'Banka Hareketi'},
];

beforeEach(()=>{
 get.mockReset();tableProps.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/finance/summary')return Promise.resolve({data:{accounts,totals:{cash:1500},instruments:{}}});
  if(url==='/finance/transactions')return Promise.resolve({data:txns});
  if(url==='/finance/instruments')return Promise.resolve({data:[]});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(entry:any='/nakit-yonetimi'){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[entry]}><Finance/><Konum/></MemoryRouter></ThemeProvider>);
}
/** Hareketler sekmesindeki tablonun o anki satır kimlikleri. */
async function rowIds(){
 await waitFor(()=>expect(tableProps).toHaveBeenCalled());
 const props=tableProps.mock.calls[tableProps.mock.calls.length-1][0];
 return props.rows.map((row:any)=>row.id);
}

it('dashboard hesap kartından gelen accountId ile hareketler sekmesini o hesaba odaklar',async()=>{
 mount({pathname:'/nakit-yonetimi',state:{accountId:55}});
 expect(await screen.findByText('Odak: Merkez Kasa')).toBeInTheDocument();
 // Yalnizca 55 numarali hesabin hareketleri kalir.
 await waitFor(async()=>expect(await rowIds()).toEqual([79,80]));
});

it('son hareket satırından gelen transactionId ile tek hareketi odaklar',async()=>{
 mount({pathname:'/nakit-yonetimi',state:{accountId:55,transactionId:79}});
 expect(await screen.findByText('Odak: Hareket #79')).toBeInTheDocument();
 await waitFor(async()=>expect(await rowIds()).toEqual([79]));
});

it('odak rozeti temizlenince tüm hareketler geri gelir',async()=>{
 mount({pathname:'/nakit-yonetimi',state:{accountId:55,transactionId:79}});
 await screen.findByText('Odak: Hareket #79');
 fireEvent.click(screen.getByTestId('CancelIcon'));
 await waitFor(async()=>expect(await rowIds()).toEqual([79,80,81]));
 expect(screen.queryByText('Odak: Hareket #79')).toBeNull();
});

it('CS3: Çek / Senet sekmesi yeni portföy sayfasına bağlantıdır; eski liste ve prompt() yok',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/finance/summary'));
 fireEvent.click(screen.getByRole('tab',{name:'Çek / Senet'}));
 expect(await screen.findByTestId('cek-senet-yonlendirme')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Çek / Senet Portföyüne Git'}));
 expect(await screen.findByTestId('konum')).toHaveTextContent('/cek-senet-portfoyu');
 // Eski `financial_instruments` listesi artık okunmuyor; müşteri/tedarikçi
 // listeleri de yalnız o formun seçicisi içindi.
 expect(get).not.toHaveBeenCalledWith('/finance/instruments');
 expect(get).not.toHaveBeenCalledWith('/customers');
 expect(get).not.toHaveBeenCalledWith('/suppliers');
});

it('CS3: sekmedeki üst düğme de yeni sayfaya gider',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/finance/summary'));
 fireEvent.click(screen.getByRole('tab',{name:'Çek / Senet'}));
 fireEvent.click(screen.getByRole('button',{name:'Çek / Senet Portföyü'}));
 expect(await screen.findByTestId('konum')).toHaveTextContent('/cek-senet-portfoyu');
});

it('CS3: kaynakta prompt() çağrısı kalmadı',()=>{
 expect(financeSource).not.toMatch(/\bprompt\(/);
 expect(financeSource).not.toContain('/finance/instruments');
});

it('router state olmadan hesaplar sekmesinde odaksız açılır',async()=>{
 mount();
 // Varsayilan sekme Hesaplar oldugundan tabloya hesap satirlari beslenir.
 await waitFor(async()=>expect(await rowIds()).toEqual([55]));
 expect(screen.queryByText(/^Odak: /)).toBeNull();
});
