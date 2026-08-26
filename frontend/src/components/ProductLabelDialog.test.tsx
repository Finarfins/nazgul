import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import ProductLabelDialog from './ProductLabelDialog';

const openAuthenticated=vi.fn();
const postAuthenticated=vi.fn();
vi.mock('../api',()=>({
 api:{},
 errorDetail:(_:unknown,fallback:string)=>fallback,
 openAuthenticated:(...args:any[])=>openAuthenticated(...args),
 postAuthenticated:(...args:any[])=>postAuthenticated(...args),
}));

const renderDialog=(productIds:number[],productName?:string)=>render(
 <ThemeProvider theme={createTheme()}>
  <ProductLabelDialog open productIds={productIds} productName={productName} onClose={vi.fn()}/>
 </ThemeProvider>,
);

/** MUI Select: tetikleyiciyi açıp açılan listeden seçeneği tıklar. */
const choose=(label:string,option:string)=>{
 fireEvent.mouseDown(screen.getByLabelText(label));
 fireEvent.click(within(screen.getByRole('listbox')).getByText(option));
};

const print=()=>fireEvent.click(screen.getByRole('button',{name:'Yazdır / İndir'}));

describe('ProductLabelDialog',()=>{
 // Vitest globals kapalı olduğu için RTL otomatik temizlik yapmaz; MUI Dialog
 // portal'ı document.body'de kalır ve sonraki testler aynı metni iki kez bulur.
 afterEach(()=>cleanup());
 beforeEach(()=>{openAuthenticated.mockReset();postAuthenticated.mockReset()});

 it('tek ürün için tekil ucu varsayılan seçeneklerle çağırır',async()=>{
  renderDialog([7],'Şanzıman Conta');
  expect(screen.getByText(/Şanzıman Conta/)).toBeInTheDocument();

  print();
  await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledTimes(1));
  const [url,filename]=openAuthenticated.mock.calls[0];
  expect(url).toContain('/products/7/barcode-label.pdf?');
  const query=new URLSearchParams(url.split('?')[1]);
  expect(query.get('format')).toBe('thermal');
  expect(query.get('show_price')).toBe('true');
  expect(query.get('price_vat')).toBe('incl');
  expect(query.get('copies')).toBe('1');
  expect(query.get('width_mm')).toBe('40');
  expect(query.get('height_mm')).toBe('30');
  expect(filename).toBe('urun-7-barkod-etiket.pdf');
  expect(postAuthenticated).not.toHaveBeenCalled();
 });

 it('fiyat kapatılınca KDV seçimi gizlenir ve show_price=false gider',async()=>{
  renderDialog([7]);
  expect(screen.getByLabelText('KDV')).toBeInTheDocument();

  fireEvent.click(screen.getByLabelText('Satış fiyatını yazdır'));
  expect(screen.queryByLabelText('KDV')).not.toBeInTheDocument();

  print();
  await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledTimes(1));
  const query=new URLSearchParams(openAuthenticated.mock.calls[0][0].split('?')[1]);
  expect(query.get('show_price')).toBe('false');
 });

 it('KDV hariç seçimi uca aktarılır',async()=>{
  renderDialog([7]);
  choose('KDV','KDV hariç');
  print();
  await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledTimes(1));
  const query=new URLSearchParams(openAuthenticated.mock.calls[0][0].split('?')[1]);
  expect(query.get('price_vat')).toBe('excl');
 });

 it('A4 seçilince termal boyut alanı gizlenir',async()=>{
  renderDialog([7]);
  expect(screen.getByLabelText('Etiket boyutu')).toBeInTheDocument();

  choose('Biçim','A4 ızgara — Code128 (sayfada 24 etiket)');
  expect(screen.queryByLabelText('Etiket boyutu')).not.toBeInTheDocument();

  print();
  await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledTimes(1));
  const query=new URLSearchParams(openAuthenticated.mock.calls[0][0].split('?')[1]);
  expect(query.get('format')).toBe('a4');
 });

 it('çok ürün seçiliyken toplu uca tek istekte gider',async()=>{
  renderDialog([3,4,5]);
  expect(screen.getByText(/3 ürün seçildi/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Kopya sayısı'),{target:{value:'2'}});
  expect(screen.getByText('Toplam 6 etiket basılacak.')).toBeInTheDocument();

  print();
  await waitFor(()=>expect(postAuthenticated).toHaveBeenCalledTimes(1));
  const [url,body,filename]=postAuthenticated.mock.calls[0];
  expect(url).toBe('/products/labels.pdf');
  expect(body).toMatchObject({
   product_ids:[3,4,5],format:'thermal',show_price:true,price_vat:'incl',
   copies:2,width_mm:40,height_mm:30,
  });
  expect(filename).toBe('urun-etiketleri.pdf');
  expect(openAuthenticated).not.toHaveBeenCalled();
 });

 it('kopya sayısı 1 ile 100 arasına sıkıştırılır',()=>{
  renderDialog([7]);
  const copies=screen.getByLabelText('Kopya sayısı') as HTMLInputElement;
  fireEvent.change(copies,{target:{value:'0'}});
  expect(copies.value).toBe('1');
  fireEvent.change(copies,{target:{value:'999'}});
  expect(copies.value).toBe('100');
 });

 it('ürün ve etiket üst sınırlarında yazdırmayı engeller',()=>{
  // 501 ürün: uç en fazla 500 kabul eder, istek hiç gönderilmemeli.
  const {unmount}=renderDialog(Array.from({length:501},(_,i)=>i+1));
  expect(screen.getByText(/en fazla 500 ürün etiketlenebilir/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Yazdır / İndir'})).toBeDisabled();
  print();
  expect(postAuthenticated).not.toHaveBeenCalled();
  unmount();

  // 100 ürün x 30 kopya = 3000 > 2000 etiket tavanı.
  renderDialog(Array.from({length:100},(_,i)=>i+1));
  fireEvent.change(screen.getByLabelText('Kopya sayısı'),{target:{value:'30'}});
  expect(screen.getByText(/en fazla 2000 etiket üretilebilir/)).toBeInTheDocument();
  print();
  expect(postAuthenticated).not.toHaveBeenCalled();
 });

 it('eski QR etiketini PARAMETRESİZ label.pdf ucundan ister',async()=>{
  // Sözleşme dondurulmuştur: sorgu dizesi eklemek sahadaki yazıcı/şablon
  // çıktısını sessizce kaydırır. Diyalog bu uca hiçbir seçenek göndermemeli.
  renderDialog([7],'Şanzıman Conta');
  choose('Biçim','Eski QR etiketi (70 × 40 mm)');

  // Seçenekler bu biçimde anlamsızdır ve gizlenir.
  expect(screen.queryByLabelText('Etiket boyutu')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Kopya sayısı')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('KDV')).not.toBeInTheDocument();

  print();
  await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledTimes(1));
  const [url,filename]=openAuthenticated.mock.calls[0];
  expect(url).toBe('/products/7/label.pdf');
  expect(url).not.toContain('?');
  expect(filename).toBe('urun-7-etiket.pdf');
  expect(postAuthenticated).not.toHaveBeenCalled();
 });

 it('toplu seçimde eski QR biçimi sunulmaz',()=>{
  // Eski uç tek üründür; toplu basım yalnızca Code128'dir.
  renderDialog([3,4,5]);
  fireEvent.mouseDown(screen.getByLabelText('Biçim'));
  expect(within(screen.getByRole('listbox')).queryByText(/Eski QR etiketi/)).toBeNull();
 });

 it('hata durumunda mesaj gösterir ve diyalog açık kalır',async()=>{
  openAuthenticated.mockRejectedValue(new Error('boom'));
  renderDialog([7]);
  print();
  expect(await screen.findByText('Etiket üretilemedi.')).toBeInTheDocument();
 });
});
