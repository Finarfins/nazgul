import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const {post}=vi.hoisted(()=>({post:vi.fn()}));

vi.mock('../api',()=>({
 api:{post},
 errorDetail:(_err:unknown,fallback:string)=>fallback,
}));

const gorunum=async()=>{
 const {default:ForgotPassword}=await import('./ForgotPassword');
 return render(<MemoryRouter><ForgotPassword/></MemoryRouter>);
};

describe('ForgotPassword',()=>{
 beforeEach(()=>{vi.resetModules();post.mockReset()});
 afterEach(cleanup);

 it('sunucunun döndürdüğü nötr mesajı gösterir',async()=>{
  // Ekran "böyle bir kullanıcı yok" DEMEMELİ: sunucu bilerek her adres için
  // aynı cevabı veriyor, arayüz de onu olduğu gibi yansıtır.
  post.mockResolvedValueOnce({
   data:{message:'Adres kayıtlıysa şifre sıfırlama bağlantısı gönderildi.'},
  });
  await gorunum();

  fireEvent.change(screen.getByLabelText(/^E-posta/),{target:{value:'patron@ornek.com'}});
  fireEvent.click(screen.getByRole('button',{name:'Sıfırlama Bağlantısı Gönder'}));

  await waitFor(()=>expect(post).toHaveBeenCalledWith('/auth/forgot-password',{
   email:'patron@ornek.com',
  }));
  expect(await screen.findByText(
   'Adres kayıtlıysa şifre sıfırlama bağlantısı gönderildi.',
  )).toBeInTheDocument();
 });

 it('e-posta boşken gönderimi kapatır',async()=>{
  await gorunum();
  expect(screen.getByRole('button',{name:'Sıfırlama Bağlantısı Gönder'})).toBeDisabled();
 });
});
