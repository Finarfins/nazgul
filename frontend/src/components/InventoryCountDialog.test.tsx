import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import InventoryCountDialog from './InventoryCountDialog';

const get=vi.fn();
const post=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:(...args:any[])=>post(...args)},
 errorDetail:(_error:any,fallback:string)=>fallback,
}));

const warehouses=[{id:1,name:'Merkez Depo'},{id:2,name:'Şube Depo'}];
const stocks=[
 {product_id:10,name:'Hidrolik Pompa',product_code:'HP-10',unit:'Adet',quantity:10,critical_stock:2,location:'A-1'},
 {product_id:11,name:'Mazot Filtresi',product_code:'MF-11',unit:'Adet',quantity:5,critical_stock:1,location:'B-2'},
];

beforeEach(()=>{
 get.mockReset();post.mockReset();
 get.mockResolvedValue({data:stocks});
 post.mockResolvedValue({data:{id:77,item_count:2,changed_count:2}});
});
afterEach(()=>cleanup());

function mount(props:any={}){
 return render(<ThemeProvider theme={createTheme()}>
  <InventoryCountDialog open warehouses={warehouses} initialWarehouseId={1}
   onClose={()=>{}} onDone={()=>{}} {...props}/>
 </ThemeProvider>);
}

async function addProduct(name:string){
 const input=screen.getByLabelText('Sayıma ürün ekle');
 fireEvent.change(input,{target:{value:name}});
 const listbox=await screen.findByRole('listbox');
 fireEvent.click(within(listbox).getByText(name));
 fireEvent.click(screen.getByRole('button',{name:'Ürün Ekle'}));
}

it('depo stoklarını yükler ve sistem miktarını satırda gösterir',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/warehouses/stock',{params:{warehouse_id:1}}));
 await addProduct('Hidrolik Pompa');
 expect(screen.getByText(/Sistem:/)).toHaveTextContent('Sistem: 10 Adet');
 expect(screen.getByDisplayValue('10')).toBeInTheDocument();
 expect(screen.getByDisplayValue('2')).toBeInTheDocument();
});

it('iki ürünlü sayımı tek POST gövdesiyle atomik endpoint’e gönderir',async()=>{
 const onDone=vi.fn();
 mount({onDone});
 await addProduct('Hidrolik Pompa');
 await addProduct('Mazot Filtresi');
 const counted=screen.getAllByLabelText('Sayılan');
 fireEvent.change(counted[0],{target:{value:'8'}});
 fireEvent.change(counted[1],{target:{value:'7'}});
 fireEvent.click(screen.getByRole('button',{name:'Sayımı Kaydet'}));
 await waitFor(()=>expect(post).toHaveBeenCalled());
 const [url,body]=post.mock.calls[0];
 expect(url).toBe('/warehouses/counts');
 expect(body.warehouse_id).toBe(1);
 expect(body.items).toEqual([
  {product_id:10,counted_quantity:8,critical_stock:2},
  {product_id:11,counted_quantity:7,critical_stock:1},
 ]);
 await waitFor(()=>expect(onDone).toHaveBeenCalledWith({id:77,item_count:2,changed_count:2}));
});

it('depo değiştiğinde stok listesini backend’den yeniden yükler',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/warehouses/stock',{params:{warehouse_id:1}}));
 fireEvent.mouseDown(screen.getByLabelText('Depo'));
 fireEvent.click(within(screen.getByRole('listbox')).getByText('Şube Depo'));
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/warehouses/stock',{params:{warehouse_id:2}}));
});

it('API hata nesnesini ham olarak ekrana basmaz',async()=>{
 post.mockRejectedValue({response:{status:500,data:{detail:[{msg:'x'}]}}});
 mount();
 await addProduct('Hidrolik Pompa');
 fireEvent.click(screen.getByRole('button',{name:'Sayımı Kaydet'}));
 expect(await screen.findByText('Stok sayımı kaydedilemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});

it('satır kaldırma düğmesi ürün adıyla erişilebilirdir',async()=>{
 mount();
 await addProduct('Hidrolik Pompa');
 fireEvent.click(screen.getByRole('button',{name:'Hidrolik Pompa sayım satırını kaldır'}));
 expect(screen.queryByText(/Sistem: 10 Adet/)).toBeNull();
 expect(screen.getByRole('button',{name:'Sayımı Kaydet'})).toBeDisabled();
});
