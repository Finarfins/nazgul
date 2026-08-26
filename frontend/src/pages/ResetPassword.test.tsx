import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

const {post}=vi.hoisted(()=>({post:vi.fn()}));

vi.mock('../api',()=>({
 api:{post},
 errorDetail:(_err:unknown,fallback:string)=>fallback,
}));

const gorunum=async(yol:string)=>{
 const {default:ResetPassword}=await import('./ResetPassword');
 return render(<MemoryRouter initialEntries={[yol]}><ResetPassword/></MemoryRouter>);
};

describe('ResetPassword',()=>{
 beforeEach(()=>{vi.resetModules();post.mockReset()});
 afterEach(cleanup);

 it('tokenı bağlantıdan alıp sıfırlama isteğine koyar',async()=>{
  post.mockResolvedValueOnce({data:{message:'ok'}});
  await gorunum('/sifre-sifirla?token=baglantidan-gelen-token');

  const [sifre,tekrar]=screen.getAllByLabelText(/^Yeni şifre/);
  fireEvent.change(sifre,{target:{value:'GuvenliParola!2026'}});
  fireEvent.change(tekrar,{target:{value:'GuvenliParola!2026'}});
  fireEvent.click(screen.getByRole('button',{name:'Şifreyi Güncelle'}));

  await waitFor(()=>expect(post).toHaveBeenCalledWith('/auth/reset-password',{
   token:'baglantidan-gelen-token',
   new_password:'GuvenliParola!2026',
  }));
  expect(await screen.findByText('Şifreniz güncellendi')).toBeInTheDocument();
 });

 it('şifreler eşleşmiyorsa istek göndermez',async()=>{
  await gorunum('/sifre-sifirla?token=gecerli-token');

  const [sifre,tekrar]=screen.getAllByLabelText(/^Yeni şifre/);
  fireEvent.change(sifre,{target:{value:'GuvenliParola!2026'}});
  fireEvent.change(tekrar,{target:{value:'BaskaParola!2026'}});
  fireEvent.click(screen.getByRole('button',{name:'Şifreyi Güncelle'}));

  expect(await screen.findByText('Şifreler eşleşmiyor.')).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
 });

 it('tokensiz açılırsa uyarır ve gönderimi kapatır',async()=>{
  await gorunum('/sifre-sifirla');

  expect(screen.getByText(/token bulunamadı/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Şifreyi Güncelle'})).toBeDisabled();
 });
});
