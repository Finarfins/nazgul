import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import ReceivablesAging,{formatDate} from './ReceivablesAging';

const get=vi.fn();
vi.mock('../api',()=>({
  api:{get:(url:string,config?:unknown)=>get(url,config)},
  money:(value:unknown)=>`${Number(value).toFixed(2)} ₺`,
  errorDetail:(_error:unknown,fallback:string)=>fallback,
}));

const report={
  as_of:'2026-07-26',
  customers:[{
    customer_id:41,customer_name:'Çiftçi Ahmet',not_due:'100.00',
    days_1_30:'200.00',days_31_60:'0.00',days_61_90:'0.00',
    days_90_plus:'50.00',total:'350.00',
    documents:[{id:7,document_no:'SAT-7',due_date:'2026-06-26',remaining:'200.00'}],
  }],
  totals:{
    not_due:'100.00',days_1_30:'200.00',days_31_60:'0.00',
    days_61_90:'0.00',days_90_plus:'50.00',total:'350.00',
  },
};

beforeEach(()=>{get.mockReset();get.mockResolvedValue({data:report})});
afterEach(()=>cleanup());

function mount(){
  return render(<ThemeProvider theme={createTheme()}><MemoryRouter><ReceivablesAging/></MemoryRouter></ThemeProvider>);
}

it.each([
  ['', ''],
  [null, null],
  [undefined, undefined],
  ['bozuk', 'bozuk'],
  ['31.12.2026', '31.12.2026'],
  ['2026-12-31', '31.12.2026'],
])('vade tarihi %s için güvenli çıktı üretir',(value,expected)=>{
  expect(formatDate(value)).toBe(expected);
});

it('matrisi ve müşteri belge drill-downını gösterir',async()=>{
  mount();
  fireEvent.click(await screen.findByText('Çiftçi Ahmet'));
  expect(screen.getByText('SAT-7')).toBeInTheDocument();
  expect(screen.getByText('Vade: 26.06.2026')).toBeInTheDocument();
  expect(screen.getByRole('link',{name:'Müşteri kartını aç'})).toHaveAttribute('href','/musteriler/41');
  expect(screen.getAllByText('350.00 ₺')).toHaveLength(2);
});

it('rapor tarihini endpoint parametresine gönderir',async()=>{
  mount();
  fireEvent.change(screen.getByLabelText('Rapor Tarihi'),{target:{value:'2026-08-26'}});
  await waitFor(()=>expect(get).toHaveBeenCalledWith(
    '/reports/receivables-aging',
    {params:{as_of:'2026-08-26'}},
  ));
});
