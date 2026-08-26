import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {ThemeProvider} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import {api} from '../api';
import {theme} from '../theme';

import SeasonalStockPlan from './SeasonalStockPlan';

vi.mock('../api',()=>({api:{get:vi.fn()},errorDetail:(_error:unknown,fallback:string)=>fallback}));
const response={data:{
 historical_year:2025,months:[6,7,8],formula:'max(historical_demand - current_stock, 0)',
 total_historical_demand:'18.5000',total_suggested_quantity:'10.5000',
 items:[
  {product_id:1,product_name:'Biçerdöver Filtresi',product_code:'BF-1',historical_demand:'12.5000',current_stock:'2',suggested_quantity:'10.5000'},
  {product_id:2,product_name:'Kayış',product_code:'K-1',historical_demand:'6',current_stock:'8',suggested_quantity:'0'},
 ],
}};
beforeEach(()=>vi.mocked(api.get).mockResolvedValue(response as never));
afterEach(()=>{cleanup();vi.clearAllMocks()});
const mount=()=>render(<ThemeProvider theme={theme}><SeasonalStockPlan/></ThemeProvider>);

describe('SeasonalStockPlan',()=>{
 it('varsayılan sezonu yükler ve öneri tablosunu gösterir',async()=>{
  mount();
  expect(await screen.findByText('Biçerdöver Filtresi')).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith('/analytics/seasonal-plan',{params:{months:'6,7,8'}});
  expect(screen.getByText('Referans yıl: 2025')).toBeInTheDocument();
  expect(screen.getByText('Önerilen tamamla: 10,5')).toBeInTheDocument();
  expect(screen.getByText(/sonuç sıfırın altındaysa öneri 0/)).toBeInTheDocument();
 });
 it('sezon seçimini ay parametresine dönüştürür',async()=>{
  mount();await screen.findByText('Biçerdöver Filtresi');
  fireEvent.mouseDown(screen.getByRole('combobox',{name:'Sezon'}));
  fireEvent.click(await screen.findByRole('option',{name:'Ekim / dikim'}));
  await waitFor(()=>expect(api.get).toHaveBeenLastCalledWith('/analytics/seasonal-plan',{params:{months:'2,3,4'}}));
 });
 it('ürün adına göre sıralayabilir',async()=>{
  mount();await screen.findByText('Biçerdöver Filtresi');
  fireEvent.mouseDown(screen.getByRole('combobox',{name:'Sıralama'}));
  fireEvent.click(await screen.findByRole('option',{name:'Ürün adı: A-Z'}));
  const names=screen.getAllByRole('row').slice(1).map(row=>row.textContent);
  expect(names[0]).toContain('Biçerdöver Filtresi');expect(names[1]).toContain('Kayış');
 });
});
