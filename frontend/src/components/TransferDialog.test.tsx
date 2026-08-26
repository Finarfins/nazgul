import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import TransferDialog from './TransferDialog';

const get=vi.fn();
const post=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:any[])=>get(...a),post:(...a:any[])=>post(...a)},
 errorDetail:(_e:any,fallback:string)=>fallback,
 policyOverrideHeaders:(reason:string)=>({'X-Policy-Override-Reason':reason}),
 policyOverrideRequired:()=>false,
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({user:{role:'admin'}})}));

const warehouses=[{id:1,name:'Merkez Depo'},{id:2,name:'Şube Depo'}];
const product={id:12,name:'Hidrolik Pompa',product_code:'HP-100',unit:'adet'};

beforeEach(()=>{
 get.mockReset();post.mockReset();
 get.mockImplementation((url:string,cfg:any)=>{
  if(url==='/warehouses/stock'){
   const wid=cfg?.params?.warehouse_id;
   return Promise.resolve({data:[{product_id:12,quantity:wid===1?40:5,unit:'adet'}]});
  }
  if(url==='/products')return Promise.resolve({data:[product]});
  return Promise.resolve({data:[]});
 });
 post.mockResolvedValue({data:{id:99}});
});
afterEach(()=>cleanup());

function mount(props:any={}){
 return render(<ThemeProvider theme={createTheme()}>
  <TransferDialog open warehouses={warehouses} onClose={()=>{}} onDone={()=>{}} {...props}/>
 </ThemeProvider>);
}

it('kaynak deposu ve ürünü önseçtiğinde mevcut stoğu backend’den gösterir',async()=>{
 mount({initialSourceId:1,initialProduct:product});
 expect(await screen.findByText(/Kaynak depo mevcut stoğu:/)).toBeInTheDocument();
 await waitFor(()=>expect(screen.getByText(/40 adet/)).toBeInTheDocument());
 // available quantity comes from GET /warehouses/stock, not invented
 expect(get).toHaveBeenCalledWith('/warehouses/stock',{params:{warehouse_id:1}});
});

it('aynı depo hedef olarak seçilemez ve buton kilitlenir',async()=>{
 mount({initialSourceId:1,initialProduct:product});
 await screen.findByText(/Kaynak depo mevcut stoğu:/);
 // target options exclude the source: only "Şube Depo" is selectable as target
 const target=screen.getByLabelText('Hedef depo');
 fireEvent.mouseDown(target);
 const listbox=within(screen.getByRole('listbox'));
 expect(listbox.queryByText('Merkez Depo')).toBeNull();
 expect(listbox.getByText('Şube Depo')).toBeInTheDocument();
});

it('geçerli transferde mevcut endpoint’e doğru gövdeyle POST atar',async()=>{
 const onDone=vi.fn();
 mount({initialSourceId:1,initialProduct:product,onDone});
 await screen.findByText(/Kaynak depo mevcut stoğu:/);
 fireEvent.click(screen.getByRole('button',{name:'Transfer Et'}));
 await waitFor(()=>expect(post).toHaveBeenCalled());
 const [url,body]=post.mock.calls[0];
 expect(url).toBe('/warehouses/transfers');
 expect(body.source_warehouse_id).toBe(1);
 expect(body.target_warehouse_id).toBe(2);
 expect(body.items).toEqual([{product_id:12,quantity:1}]);
 await waitFor(()=>expect(onDone).toHaveBeenCalled());
});

it('ürün seçili değilken transfer engellenir ve hata düz metindir',async()=>{
 mount({initialSourceId:1});
 // no product preselected and picker empty → submit shows guard text, no POST
 fireEvent.click(await screen.findByRole('button',{name:'Transfer Et'}));
 expect(await screen.findByText('Kaynak depo, hedef depo ve ürün seçin.')).toBeInTheDocument();
 expect(post).not.toHaveBeenCalled();
});

it('backend hatası ham nesne yerine güvenli metinle gösterilir',async()=>{
 post.mockRejectedValue({response:{status:409,data:{detail:'Yetersiz stok'}}});
 mount({initialSourceId:1,initialProduct:product});
 await screen.findByText(/Kaynak depo mevcut stoğu:/);
 fireEvent.click(screen.getByRole('button',{name:'Transfer Et'}));
 expect(await screen.findByText('Transfer oluşturulamadı.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});
