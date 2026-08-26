import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import {PASSWORD_MIN_LENGTH,PASSWORD_RULE_LABEL} from '../passwordPolicy';

const {post}=vi.hoisted(()=>({post:vi.fn()}));

vi.mock('../api',()=>({
 api:{post},
 errorDetail:()=> 'Kayıt oluşturulamadı.',
}));

describe('Register Turnstile yaşam döngüsü',()=>{
 beforeEach(()=>{
  vi.resetModules();
  vi.stubEnv('VITE_TURNSTILE_SITE_KEY','test-site-key');
  post.mockReset();
 });

 it('başarısız kayıt denemesinden sonra tokenı temizler ve widgetı resetler',async()=>{
  const reset=vi.fn();
  window.turnstile={
   render:(_element,options)=>{queueMicrotask(()=>options.callback('single-use-token'));return 'widget-7'},
   reset,
  };
  post.mockRejectedValueOnce(new Error('rejected'));
  const {default:Register}=await import('./Register');
  render(<MemoryRouter><Register/></MemoryRouter>);

  fireEvent.change(screen.getByLabelText(/^Ad Soyad/),{target:{value:'Ayşe Test'}});
  fireEvent.change(screen.getByLabelText(/^Şirket Ünvanı \/ Şahıs Adı/),{target:{value:'Test Şirketi'}});
  fireEvent.change(screen.getByLabelText(/^E-posta/),{target:{value:'ayse@example.com'}});
  fireEvent.change(screen.getByLabelText(/^Cep Telefonu/),{target:{value:'5361234567'}});
  const [password,passwordConfirmation]=screen.getAllByLabelText(/^Şifre/);
  fireEvent.change(password,{target:{value:'GuvenliParola!2026'}});
  fireEvent.change(passwordConfirmation,{target:{value:'GuvenliParola!2026'}});
  fireEvent.click(screen.getByLabelText('Kullanım Koşullarını kabul ediyorum'));

  const submit=await screen.findByRole('button',{name:'Kayıt ol'});
  await waitFor(()=>expect(submit).toBeEnabled());
  fireEvent.click(submit);

  await waitFor(()=>expect(post).toHaveBeenCalledWith('/auth/register',expect.objectContaining({
   turnstile_token:'single-use-token',
  })));
  await waitFor(()=>expect(reset).toHaveBeenCalledWith('widget-7'));
  expect(submit).toBeDisabled();
 });
});

describe('Register parola politikası',()=>{
 beforeEach(()=>{
  // vitest `globals` kapalı olduğu için Testing Library kendi otomatik
  // temizliğini kuramıyor; render'lar dosya boyunca birikmesin diye elle.
  cleanup();
  vi.resetModules();
  vi.stubEnv('VITE_TURNSTILE_SITE_KEY','');
  post.mockReset();
 });

 const fillRequiredFields=(password:string)=>{
  fireEvent.change(screen.getByLabelText(/^Ad Soyad/),{target:{value:'Ayşe Test'}});
  fireEvent.change(screen.getByLabelText(/^Şirket Ünvanı \/ Şahıs Adı/),{target:{value:'Test Şirketi'}});
  fireEvent.change(screen.getByLabelText(/^E-posta/),{target:{value:'ayse@example.com'}});
  fireEvent.change(screen.getByLabelText(/^Cep Telefonu/),{target:{value:'5361234567'}});
  const [passwordField,confirmationField]=screen.getAllByLabelText(/^Şifre/);
  fireEvent.change(passwordField,{target:{value:password}});
  fireEvent.change(confirmationField,{target:{value:password}});
 };

 it('yalnız tek kural gösterir; karmaşıklık şartları listelenmez',async()=>{
  const {default:Register}=await import('./Register');
  render(<MemoryRouter><Register/></MemoryRouter>);

  expect(screen.getByText(new RegExp(PASSWORD_RULE_LABEL))).toBeInTheDocument();
  for(const removed of ['Büyük harf','Küçük harf','Rakam','Özel karakter']){
   expect(screen.queryByText(new RegExp(removed))).toBeNull();
  }
 });

 it(`${PASSWORD_MIN_LENGTH} karakterli parolayı kabul eder, bir eksiğini reddeder`,async()=>{
  const {default:Register}=await import('./Register');
  render(<MemoryRouter><Register/></MemoryRouter>);
  const submit=screen.getByRole('button',{name:'Kayıt ol'});
  fireEvent.click(screen.getByLabelText('Kullanım Koşullarını kabul ediyorum'));

  fillRequiredFields('a'.repeat(PASSWORD_MIN_LENGTH-1));
  await waitFor(()=>expect(submit).toBeDisabled());

  fillRequiredFields('a'.repeat(PASSWORD_MIN_LENGTH));
  await waitFor(()=>expect(submit).toBeEnabled());
 });
});
