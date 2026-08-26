import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import SupplierPriceImportDialog,{unmatchedBarcodeCsv} from './SupplierPriceImportDialog';
import {api} from '../api';

vi.mock('../api',()=>({api:{get:vi.fn(),post:vi.fn()},errorDetail:(_error:unknown,fallback:string)=>fallback}));

describe('Tedarikçi fiyat listesi import',()=>{
 beforeEach(()=>{vi.clearAllMocks();vi.mocked(api.get).mockResolvedValue({data:[{id:4,name:'Tarım Parça'}]} as never)});
 it('tedarikçi ve dosyayı gönderip kısmi başarı özetini gösterir',async()=>{
  vi.mocked(api.post).mockResolvedValue({data:{processed_rows:3,matched:1,inserted:1,updated:0,unmatched_count:1,unmatched_barcodes:['869X'],invalid_price_count:1,invalid_rows:[{row:4,barcode:'869BAD',message:'MOQ geçersiz'}],foreign_currency_rows:1,currencies_requiring_rate:['EUR']}} as never);
  const imported=vi.fn();render(<SupplierPriceImportDialog open onClose={vi.fn()} onImported={imported}/>);
  fireEvent.mouseDown(await screen.findByRole('combobox',{name:/Tedarikçi/}));fireEvent.click(await screen.findByText('Tarım Parça'));
  const input=document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input,{target:{files:[new File(['Barkod,Fiyat'], 'liste.csv',{type:'text/csv'})]}});
  fireEvent.click(screen.getByRole('button',{name:'Listeyi Yükle'}));
  await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/supplier-price-lists/import',expect.any(FormData)));
  expect(await screen.findByText('1 eşleşti · 1 barkod bulunamadı · 1 geçersiz fiyat')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Eşleşmeyenleri İndir'})).toBeInTheDocument();
  expect(screen.getByText('Satır 4 · 869BAD: MOQ geçersiz')).toBeInTheDocument();
  expect(screen.getByText('1 dövizli satır · EUR')).toBeInTheDocument();expect(imported).toHaveBeenCalled();
 });
 it('indirilen CSV formül enjeksiyonunu etkisizleştirir',()=>{
  expect(unmatchedBarcodeCsv(['=1+1','869000'])).toContain('"\'=1+1"');
 });
});
