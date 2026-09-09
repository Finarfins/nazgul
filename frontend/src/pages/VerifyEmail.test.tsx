import React from 'react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const {post}=vi.hoisted(()=>({post:vi.fn()}));

vi.mock('../api',()=>({
 api:{post},
 errorDetail:(_err:unknown,fallback:string)=>fallback,
}));

// H18 — sayfa `main.tsx` içinde <React.StrictMode> altında yaşıyor; geliştirme
// modunda React her efekti bilerek iki kez çalıştırır. Testler de aynı çatıyı
// kurar ki ikinci POST'u (tek kullanımlık token → 400 → başarı ekranının
// üstüne hata) gerçek koşullarda yakalayalım.
const gorunum=async(entry:string)=>{
 const {default:VerifyEmail}=await import('./VerifyEmail');
 return render(
  <React.StrictMode>
   <MemoryRouter initialEntries={[entry]}><VerifyEmail/></MemoryRouter>
  </React.StrictMode>,
 );
};

describe('VerifyEmail',()=>{
 beforeEach(()=>{vi.resetModules();post.mockReset()});
 afterEach(cleanup);

 it('StrictMode çift çağrısına rağmen tokeni tek kez harcar',async()=>{
  post.mockResolvedValue({data:{message:'E-posta adresiniz doğrulandı.'}});
  const {rerender}=await gorunum('/e-posta-dogrula?token=abc');

  expect(await screen.findByText('E-posta adresiniz doğrulandı.')).toBeInTheDocument();
  // MUTASYON KAPISI: VerifyEmail.tsx'teki `consumed` useRef koruması
  // kaldırılırsa StrictMode'un ikinci efekti ikinci bir POST atar ve bu
  // beklenti 2 çağrı görüp kırmızıya döner.
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith('/auth/verify-email',{token:'abc'});

  // Aynı token'la yeniden render — efekt bağımlılığı `params` kimliği
  // değişse bile ikinci istek çıkmamalı.
  const {default:VerifyEmail}=await import('./VerifyEmail');
  rerender(
   <React.StrictMode>
    <MemoryRouter initialEntries={['/e-posta-dogrula?token=abc']}><VerifyEmail/></MemoryRouter>
   </React.StrictMode>,
  );
  await waitFor(()=>expect(post).toHaveBeenCalledTimes(1));
 });

 it('sunucu 400 dönerse hata metnini basar ve isteği tekrarlamaz',async()=>{
  post.mockRejectedValue({response:{status:400,data:{detail:'Token geçersiz.'}}});
  await gorunum('/e-posta-dogrula?token=bayat');

  expect(await screen.findByText('E-posta doğrulanamadı.')).toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
 });

 it('token yokken hiç POST atmaz',async()=>{
  await gorunum('/e-posta-dogrula');

  expect(await screen.findByText('Doğrulama bağlantısında token bulunamadı.')).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
 });
});
