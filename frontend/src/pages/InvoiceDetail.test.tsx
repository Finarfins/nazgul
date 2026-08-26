import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {api} from '../api';
import {EInvoiceStatusPanel,type EInvoiceStatus} from './InvoiceDetail';

vi.mock('../api',()=>({
 api:{get:vi.fn(),post:vi.fn()},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
 money:(value:unknown)=>String(value??''),
}));

const statusResponse=(status:EInvoiceStatus,lastError:string|null=null)=>({
 data:{
  einvoice_channel:null,einvoice_status:status,einvoice_uuid:null,
  einvoice_external_id:null,einvoice_last_error:lastError,
  einvoice_submitted_at:null,einvoice_updated_at:null,
 },
});

describe('InvoiceDetail e-Fatura durumu',()=>{
 beforeEach(()=>vi.clearAllMocks());
 afterEach(()=>cleanup());

 it.each([
  ['NONE','e-Fatura: yapılandırılmamış'],
  ['PENDING','e-Fatura: bekliyor'],
  ['SENT','e-Fatura: gönderildi'],
  ['ACCEPTED','e-Fatura: kabul edildi'],
  ['REJECTED','e-Fatura: reddedildi'],
  ['ERROR','e-Fatura: hata'],
 ] as const)('%s durumunu doğru rozetle gösterir',async(status,label)=>{
  vi.mocked(api.get).mockResolvedValue(statusResponse(status) as never);
  render(<EInvoiceStatusPanel invoiceId={17}/>);
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith('/invoices/17/einvoice/status');
 });

 it('NoOp cevabını bilgi mesajı ve devre dışı sağlayıcı durumu olarak gösterir',async()=>{
  vi.mocked(api.get).mockResolvedValue(statusResponse('NONE') as never);
  vi.mocked(api.post).mockResolvedValue({data:{
   ...statusResponse('NONE').data,
   message:'e-Fatura sağlayıcısı yapılandırılmamış',
  }} as never);
  render(<EInvoiceStatusPanel invoiceId={23}/>);
  const submit=await screen.findByRole('button',{name:'e-Fatura Gönder'});
  fireEvent.click(submit);
  expect(await screen.findByText('e-Fatura sağlayıcısı yapılandırılmamış')).toBeInTheDocument();
  const disabled=screen.getByRole('button',{name:'Sağlayıcı yok'});
  expect(disabled).toBeDisabled();
  expect(screen.getByText(/Altyapı hazır/)).toBeInTheDocument();
  expect(api.post).toHaveBeenCalledWith('/invoices/23/einvoice/submit');
 });

 it('ERROR durumunda last_error ayrıntısını görünür kılar',async()=>{
  vi.mocked(api.get).mockResolvedValue(statusResponse('ERROR','Sağlayıcı zaman aşımına uğradı') as never);
  render(<EInvoiceStatusPanel invoiceId={29}/>);
  expect(await screen.findByText('e-Fatura: hata')).toBeInTheDocument();
  expect(screen.getByText('Sağlayıcı zaman aşımına uğradı')).toBeInTheDocument();
  await waitFor(()=>expect(screen.getByRole('button',{name:'e-Fatura Gönder'})).toBeEnabled());
 });
});
