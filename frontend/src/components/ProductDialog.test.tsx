import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,describe,expect,it,vi} from 'vitest';
import ProductDialog from './ProductDialog';

vi.mock('../AuthContext',()=>({useAuth:()=>({user:{role:'admin'}})}));
// vi.hoisted şart: vi.mock fabrikası modül gövdesinden ÖNCE çalışır, düz bir
// `const` casusa erişemez (TDZ) ve mock sessizce bozulur.
const {get,put}=vi.hoisted(()=>({
 get:vi.fn(),
 put:vi.fn(),
}));
vi.mock('../api',()=>({
 api:{get,post:vi.fn(),put},
 policyOverrideHeaders:vi.fn(),
 policyOverrideRequired:()=>false,
}));

// Testler arasi temizlik: yoksa onceki diyalog DOM'da kalir ve ikinci test
// 'birden fazla eleman bulundu' diye duser.
afterEach(cleanup);

describe('ProductDialog',()=>{
 it('hızlı ürün açılışında taranan barkod ve ürün kodunu önceden doldurur',()=>{
  render(<ThemeProvider theme={createTheme()}><ProductDialog open initialValues={{barcode:'8699999999999',product_code:'YENI-KOD'}} onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);
  expect(screen.getByLabelText('Barkod')).toHaveValue('8699999999999');
  expect(screen.getByLabelText('Ürün kodu')).toHaveValue('YENI-KOD');
 });

 // products.active kolonu ve POS filtresi zaten vardı; değiştirecek bir yüzey
 // yoktu, yani bir ürünü satışa kapatmak imkânsızdı.
 it('ürünü satışa kapatıp kaydeder',async()=>{
  get.mockResolvedValue({data:{product:{
   id:7,name:'Satışa Kapanacak',sale_price:100,purchase_price:0,vat_rate:20,
   unit:'Adet',stock:0,active:true,
  }}});
  put.mockResolvedValue({data:{id:7}});
  render(<ThemeProvider theme={createTheme()}><ProductDialog open id={7} onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);
  await waitFor(()=>expect(screen.getByLabelText('Ürün adı')).toHaveValue('Satışa Kapanacak'));
  const anahtar=screen.getByRole('switch');
  expect(anahtar).toBeChecked();

  fireEvent.click(anahtar);
  expect(screen.getByText('Satışa kapalı')).toBeInTheDocument();
  // Silmek değil kapatmak: geçmiş satışlar ürüne bağlı kalır.
  expect(screen.getByText(/POS ve hızlı seçim listelerinde çıkmaz/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button',{name:/Kaydet|Güncelle/}));
  await waitFor(()=>expect(put).toHaveBeenCalled());
  expect(put.mock.calls[0][1]).toMatchObject({active:false});
 });
});
