import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {AxiosError,AxiosHeaders} from 'axios';
import {MemoryRouter} from 'react-router-dom';

import {unwrapApiError} from '../api';
import Login from './Login';

const login=vi.fn();
vi.mock('../AuthContext',()=>({useAuth:()=>({login})}));

// Hata, sayfaya axios yanıt interceptor'ından (unwrapApiError) GEÇMİŞ hâliyle
// verilir: ham `{response:{data:{detail:{code}}}}` taklidi interceptor'ın
// dict detail'i metne çevirmesini atlar ve ölü `code` dalını yeşil gösterirdi.
const interceptedError=(status:number,detail:unknown)=>{
 const error=new AxiosError(`Request failed with status code ${status}`);
 error.response={status,statusText:'',headers:{},config:{headers:new AxiosHeaders()},data:{detail}};
 return unwrapApiError(error);
};

describe('Giriş',()=>{afterEach(()=>{cleanup();login.mockReset()});
 it('e-posta doğrulaması gerekiyorsa sunucu metni yerine doğrulama ipucunu gösterir',async()=>{
  login.mockRejectedValue(interceptedError(403,{code:'EMAIL_VERIFICATION_REQUIRED',message:'Sunucu metni'}));
  render(<MemoryRouter><Login/></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Kullanıcı adı veya e-posta'),{target:{value:'ilk@example.com'}});
  fireEvent.change(screen.getByLabelText('Şifre'),{target:{value:'Parola!123'}});
  fireEvent.click(screen.getByRole('button',{name:'Giriş Yap'}));
  expect(await screen.findByText('Giriş yapmadan önce e-posta adresinizi doğrulayın.')).toBeInTheDocument();
  expect(screen.queryByText('Sunucu metni')).not.toBeInTheDocument();
 });
 it('kodsuz hata gövdesinde sunucunun mesajını gösterir',async()=>{
  login.mockRejectedValue(interceptedError(401,'Kullanıcı adı veya şifre hatalı'));
  render(<MemoryRouter><Login/></MemoryRouter>);
  fireEvent.click(screen.getByRole('button',{name:'Giriş Yap'}));
  expect(await screen.findByText('Kullanıcı adı veya şifre hatalı')).toBeInTheDocument();
 });
});
