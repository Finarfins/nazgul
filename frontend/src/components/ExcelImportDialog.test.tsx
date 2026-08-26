import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,describe,expect,it,vi} from 'vitest';
import ExcelImportDialog from './ExcelImportDialog';

// vi.hoisted şart: vi.mock fabrikası modül gövdesinden önce çalışır.
const {post}=vi.hoisted(()=>({post:vi.fn()}));
vi.mock('../AuthContext',()=>({useAuth:()=>({user:{role:'admin'},can:()=>true})}));
vi.mock('../api',()=>({
 api:{post,get:vi.fn()},
 policyOverrideHeaders:()=>({}),
 policyOverrideRequired:()=>false,
}));

afterEach(cleanup);

const mount=()=>render(<ThemeProvider theme={createTheme()}>
 <ExcelImportDialog open kind="sales" onClose={vi.fn()} onDone={vi.fn()}/>
</ThemeProvider>);

const secDosya=()=>{
 const input=document.querySelector('input[type=file]') as HTMLInputElement;
 const file=new File(['x'],'satis.xlsx',{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
 fireEvent.change(input,{target:{files:[file]}});
};

describe('ExcelImportDialog',()=>{
 // Genel istemcinin 15 sn'lik zaman aşımı toplu içe aktarma için çok kısa.
 // Süre dolunca tarayıcı isteği keser ama sunucu işi bitirip KAYDEDER:
 // kullanıcı "başarısız" görür, kayıtlar aslında girmiştir. Gerçek kurulumda
 // 439 satış belgesi böyle oluştu ve ekranda hata çıktı.
 it('toplu içe aktarmaya uzun zaman aşımı verir',async()=>{
  post.mockResolvedValue({data:{inserted:1,updated:0,errors:[],total_rows:1}});
  mount();
  secDosya();
  fireEvent.click(screen.getByRole('button',{name:/Yükle ve İşle/}));
  await waitFor(()=>expect(post).toHaveBeenCalled());
  const config=post.mock.calls[0][2];
  expect(config.timeout).toBeGreaterThanOrEqual(120000);
 });

 it('sunucunun hata açıklamasını gösterir',async()=>{
  post.mockRejectedValue({response:{data:{detail:'Önce bir depo oluşturun.'}}});
  mount();
  secDosya();
  fireEvent.click(screen.getByRole('button',{name:/Yükle ve İşle/}));
  // Kuru "Aktarım başarısız." yerine sunucunun söylediği sebep görünmeli.
  await screen.findByText('Önce bir depo oluşturun.');
 });
});
