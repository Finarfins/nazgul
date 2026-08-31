import React from 'react';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import WorkOrders from './WorkOrders';

const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 money:(value:any)=>String(value),
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({can:()=>true})}));
vi.mock('../components/ResponsiveTable',()=>({default:()=>null}));
const dialogProps=vi.fn();
vi.mock('../components/WorkOrderDialog',()=>({default:(props:any)=>{dialogProps(props);return null}}));

beforeEach(()=>{
 get.mockReset();dialogProps.mockReset();
 get.mockImplementation(()=>Promise.resolve({data:{items:[]}}));
});
afterEach(()=>cleanup());

function mount(entry='/is-emirleri'){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[entry]}><WorkOrders/></MemoryRouter></ThemeProvider>);
}

it('dashboard ?new=1 kısayoluyla yeni iş emri formunu doğrudan açar',async()=>{
 mount('/is-emirleri?new=1');
 await waitFor(()=>{
  const props=dialogProps.mock.calls[dialogProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.id).toBeNull();
 });
});

it('parametresiz açılışta iş emri formunu kendiliğinden açmaz',async()=>{
 mount();
 await waitFor(()=>expect(dialogProps).toHaveBeenCalled());
 const props=dialogProps.mock.calls[dialogProps.mock.calls.length-1][0];
 expect(props.open).toBe(false);
});
