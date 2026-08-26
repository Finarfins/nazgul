import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import SupplierPriceHistory,{SupplierPriceHistoryTab} from './SupplierPriceHistory';
import {api} from '../api';

vi.mock('../api',()=>({
  api:{get:vi.fn()},
  errorDetail:(_:unknown,fallback:string)=>fallback,
  money:(value:unknown)=>`${value} TL`,
}));

describe('SupplierPriceHistory',()=>{
  beforeEach(()=>vi.clearAllMocks());

  it('lists newest supplier observations from the scoped endpoint',async()=>{
    vi.mocked(api.get).mockResolvedValueOnce({data:{items:[{
      id:2,product_name:'Yağ Filtresi',price:'4.0000',currency:'EUR',
      price_in_try:'140.0000',source:'MANUAL',note:'Ağustos listesi',
      captured_at:'2026-08-01T09:00:00+00:00',
    }],has_more:true,next_cursor:'next-page',supplier_id:7}} as any)
      .mockResolvedValueOnce({data:{items:[{
        id:1,product_name:'Hava Filtresi',price:'3.0000',currency:'EUR',
        price_in_try:'105.0000',source:'PURCHASE',note:null,
        captured_at:'2026-07-01T09:00:00+00:00',
      }],has_more:false,next_cursor:null,supplier_id:7}} as any);

    render(<SupplierPriceHistory supplierId={7}/>);

    await waitFor(()=>expect(api.get).toHaveBeenCalledWith('/supplier-prices/history',{params:{supplier_id:7}}));
    expect(await screen.findByText('Yağ Filtresi')).toBeInTheDocument();
    expect(screen.getByText('Manuel')).toBeInTheDocument();
    expect(screen.getByText('4.0000 EUR')).toBeInTheDocument();
    expect(screen.getByText('140.0000 TL')).toBeInTheDocument();
    expect(screen.getByText(/Ağustos listesi/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Daha Fazla Yükle'}));
    expect(await screen.findByText('Hava Filtresi')).toBeInTheDocument();
    expect(api.get).toHaveBeenLastCalledWith('/supplier-prices/history',{params:{supplier_id:7,cursor:'next-page'}});
  });

  it('shows an explicit empty state',async()=>{
    vi.mocked(api.get).mockResolvedValue({data:{items:[],has_more:false,next_cursor:null,supplier_id:8}} as any);
    render(<SupplierPriceHistory supplierId={8}/>);
    expect(await screen.findByText('Kayıtlı fiyat geçmişi yok.')).toBeInTheDocument();
  });

  it('hides the commercial history tab without purchases permission',()=>{
    const {rerender}=render(<SupplierPriceHistoryTab allowed={false}/>);
    expect(screen.queryByRole('tab',{name:'Fiyat Geçmişi'})).not.toBeInTheDocument();
    rerender(<SupplierPriceHistoryTab allowed/>);
    expect(screen.getByRole('tab',{name:'Fiyat Geçmişi'})).toBeInTheDocument();
  });

  it('ignores a late continuation response after the supplier changes',async()=>{
    let resolveLate:(value:any)=>void=()=>{};
    const latePage=new Promise(resolve=>{resolveLate=resolve});
    vi.mocked(api.get)
      .mockResolvedValueOnce({data:{supplier_id:1,items:[{
        id:11,product_id:1,product_name:'A İlk',price:'10',currency:'TRY',
        price_in_try:'10',source:'MANUAL',note:null,captured_at:'2026-08-01T09:00:00Z',
      }],has_more:true,next_cursor:'a-next'}} as any)
      .mockReturnValueOnce(latePage as any)
      .mockResolvedValueOnce({data:{supplier_id:2,items:[{
        id:21,product_id:2,product_name:'B İlk',price:'20',currency:'TRY',
        price_in_try:'20',source:'MANUAL',note:null,captured_at:'2026-08-02T09:00:00Z',
      }],has_more:false,next_cursor:null}} as any);

    const {rerender}=render(<SupplierPriceHistory supplierId={1}/>);
    expect(await screen.findByText('A İlk')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Daha Fazla Yükle'}));
    rerender(<SupplierPriceHistory supplierId={2}/>);
    expect(await screen.findByText('B İlk')).toBeInTheDocument();

    await act(async()=>{
      resolveLate({data:{supplier_id:1,items:[{
        id:12,product_id:1,product_name:'A Geciken',price:'9',currency:'TRY',
        price_in_try:'9',source:'MANUAL',note:null,captured_at:'2026-07-01T09:00:00Z',
      }],has_more:false,next_cursor:null}});
      await latePage;
    });

    await waitFor(()=>expect(screen.queryByText('A Geciken')).not.toBeInTheDocument());
    expect(screen.getByText('B İlk')).toBeInTheDocument();
  });
});
