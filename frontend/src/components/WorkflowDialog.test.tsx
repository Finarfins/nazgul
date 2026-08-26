import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import WorkflowDialog from './WorkflowDialog';

const get=vi.fn();const post=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:(...args:any[])=>post(...args),put:vi.fn()},
 money:(v:any)=>`${Number(v).toFixed(2)} ₺`,
 policyOverrideHeaders:(reason:string)=>({'X-Policy-Override-Reason':reason}),
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({user:{role:'admin'}})}));

const product={id:12,name:'Hidrolik Pompa',product_code:'HP-100',sale_price:1500,purchase_price:1000,vat_rate:20};

beforeEach(()=>{
 get.mockReset();post.mockReset();post.mockResolvedValue({data:{id:1}});
 get.mockImplementation((url:string)=>{
  if(url==='/customers')return Promise.resolve({data:[{id:7,name:'Ali Çiftçi'}]});
  if(url==='/products')return Promise.resolve({data:[product]});
  if(url==='/warehouses')return Promise.resolve({data:[{id:3,name:'Merkez Depo'}]});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(props:any={}){
 return render(<ThemeProvider theme={createTheme()}>
  <WorkflowDialog open kind="quote" onClose={()=>{}} onSaved={()=>{}} {...props}/>
 </ThemeProvider>);
}

async function pick(label:string,option:RegExp){
 const input=screen.getAllByLabelText(label)[0];
 fireEvent.mouseDown(input);fireEvent.change(input,{target:{value:''}});
 const list=await screen.findByRole('listbox');
 fireEvent.click(within(list).getByText(option));
}

it('ürün seçilince altına boş satır kendiliğinden açılır',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 expect(screen.getAllByLabelText('Miktar')).toHaveLength(1);
 await pick('Ürün',/Hidrolik Pompa/);
 await waitFor(()=>expect(screen.getAllByLabelText('Miktar')).toHaveLength(2));
 const deleteButtons=screen.getAllByRole('button',{name:'Satırı sil'});
 expect(deleteButtons[1]).toBeDisabled();
});

it('otomatik açılan boş satır kayda gitmez',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 await pick('Müşteri',/Ali Çiftçi/);
 await pick('Ürün',/Hidrolik Pompa/);
 await waitFor(()=>expect(screen.getAllByLabelText('Miktar')).toHaveLength(2));
 fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
 await waitFor(()=>expect(post).toHaveBeenCalled());
 const payload=post.mock.calls[0][1];
 expect(payload.items).toHaveLength(1);
 expect(payload.items[0].product_id).toBe(12);
});

it('Kalem Ekle zaten boş satır varken ikinci boş satır açmaz',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 fireEvent.click(screen.getByRole('button',{name:/Kalem Ekle/}));
 expect(screen.getAllByLabelText('Miktar')).toHaveLength(1);
});
