/**
 * F10-1d — cari kartında çiftçi WhatsApp bağlantısı.
 *
 * Ölçülenler: (1) izin kapısı K4 ile birebir (CUSTOMER -> `sales`,
 * SUPPLIER -> `purchases`) ve izinsiz rolde HİÇBİR uç çağrılmaz; (2) mount'ta
 * yazma ucu çağrılmaz; (3) düz kod yalnız pencerede, pencere kapanınca DOM'da
 * yok; (4) bağlantı kapatma onaydan SONRA DELETE atar ve listeyi yeniler;
 * (5) maskeli telefon istemcide açılmaz ve ön dolguya girmez.
 */
import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import WhatsAppTarafKarti from './WhatsAppTarafKarti';

const get=vi.fn();
const post=vi.fn();
const del=vi.fn();
vi.mock('../api',()=>({
 api:{
  get:(...a:unknown[])=>get(...a),post:(...a:unknown[])=>post(...a),
  put:vi.fn(),delete:(...a:unknown[])=>del(...a),
 },
 errorDetail:(e:unknown,fallback:string)=>{
  const detay=(e as {response?:{data?:{detail?:unknown}}})?.response?.data?.detail;
  if(typeof detay==='string'&&detay)return detay;
  const mesaj=(detay as {message?:unknown}|undefined)?.message;
  return typeof mesaj==='string'&&mesaj?mesaj:fallback;
 },
}));
let izinler:string[]=[];
vi.mock('../AuthContext',()=>({useAuth:()=>({can:(izin:string)=>izinler.includes('*')||izinler.includes(izin)})}));

const DUZ_KOD='K7Q2-M9XA';
const AKTIF={id:11,party_type:'CUSTOMER',party_id:7,phone_masked:'***2233',is_active:true,
 consent_at:'2026-09-20T21:30:00+00:00',created_at:'2026-09-20T21:00:00'};
const RIZASIZ={...AKTIF,id:12,phone_masked:'***4455',consent_at:null};
const PASIF={...AKTIF,id:9,phone_masked:'***0000',is_active:false};

let linkler:unknown[]=[];
beforeEach(()=>{
 cleanup();izinler=['read','sales','purchases'];linkler=[AKTIF,RIZASIZ,PASIF];
 get.mockReset();post.mockReset();del.mockReset();
 get.mockImplementation(()=>Promise.resolve({data:linkler}));
 post.mockResolvedValue({data:{
  kod_id:31,kod:DUZ_KOD.replace('-',''),kod_gosterim:DUZ_KOD,
  expires_at:new Date(Date.now()+10*60000).toISOString(),
  party_type:'CUSTOMER',party_id:7,telefon:'***1122',
 }});
 del.mockResolvedValue({data:{}});
});
afterEach(()=>cleanup());

const mount=(partyType:'CUSTOMER'|'SUPPLIER'='CUSTOMER',defaultPhone:string|null='05321112233')=>
 render(<WhatsAppTarafKarti partyType={partyType} partyId={7} defaultPhone={defaultPhone}/>);

describe('F10-1d — izin kapısı (K4)',()=>{
 it.each([
  ['CUSTOMER','sales'],
  ['SUPPLIER','purchases'],
 ] as const)('%s: %s ile kart çizilir ve liste doğru taraf tipiyle okunur',async(tip,izin)=>{
  izinler=['read',izin];
  mount(tip);
  expect(await screen.findByText('WhatsApp bağlantısı')).toBeTruthy();
  await screen.findByText('***2233');
  expect(get).toHaveBeenCalledWith('/whatsapp/party-links',{params:{party_type:tip,party_id:7}});
  expect(screen.getByRole('button',{name:'Eşleştirme kodu üret'})).toBeTruthy();
  expect(screen.getAllByRole('button',{name:/bağlantısını kapat/})).toHaveLength(2);
 });

 it.each([
  ['CUSTOMER','purchases'],
  ['SUPPLIER','sales'],
 ] as const)('%s: yalnız %s taşıyan rolde kart YOK ve hiçbir uç çağrılmaz',async(tip,izin)=>{
  izinler=['read',izin];
  const {container}=mount(tip);
  await new Promise(r=>setTimeout(r,0));
  expect(container.innerHTML).toBe('');
  expect(screen.queryByRole('button',{name:'Eşleştirme kodu üret'})).toBeNull();
  expect(get).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
  expect(del).not.toHaveBeenCalled();
 });

 it('yönetici (*) iki taraf tipinde de kartı görür',async()=>{
  izinler=['*'];
  mount('SUPPLIER');
  await screen.findByText('***2233');
  cleanup();
  mount('CUSTOMER');
  await screen.findByText('***2233');
  expect(get.mock.calls.map(c=>(c[1] as {params:{party_type:string}}).params.party_type))
   .toEqual(['SUPPLIER','CUSTOMER']);
 });
});

describe('F10-1d — gösterim',()=>{
 it('mount yalnız GET atar; yazma ucu çağrılmaz',async()=>{
  mount();
  await screen.findByText('***2233');
  expect(get).toHaveBeenCalledTimes(1);
  expect(post).not.toHaveBeenCalled();
  expect(del).not.toHaveBeenCalled();
 });

 it('aktif bağlantılar maskeli numara ve rıza rozetiyle; pasif satır çizilmez',async()=>{
  mount();
  const rizali=await screen.findByTestId('wa-baglanti-11');
  expect(within(rizali).getByText('Rıza var')).toBeTruthy();
  expect(within(rizali).getByText(/^Rıza: \d{4}-\d{2}-\d{2}$/)).toBeTruthy();
  const rizasiz=screen.getByTestId('wa-baglanti-12');
  expect(within(rizasiz).getByText('Rıza yok')).toBeTruthy();
  expect(within(rizasiz).getByText('Rıza çiftçinin ilk mesajıyla alınır')).toBeTruthy();
  expect(screen.queryByText('***0000')).toBeNull();
 });

 it('consent_at UTC anı YEREL güne çevrilir (utils/tarih yerelGun)',async()=>{
  const {yerelGun}=await import('../utils/tarih');
  mount();
  const rizali=await screen.findByTestId('wa-baglanti-11');
  expect(within(rizali).getByText(`Rıza: ${yerelGun(AKTIF.consent_at)}`)).toBeTruthy();
 });

 it('bağlantı yoksa boş durum metni',async()=>{
  linkler=[];
  mount();
  expect(await screen.findByText('Bağlı numara yok.')).toBeTruthy();
 });

 it('ham telefon ön dolguya girer; maskeli telefon GİRMEZ',async()=>{
  mount('CUSTOMER','05321112233');
  await screen.findByText('***2233');
  expect((screen.getByLabelText('Çiftçinin cep telefonu') as HTMLInputElement).value).toBe('05321112233');
  cleanup();
  mount('CUSTOMER','05** *** ** 33');
  await screen.findByText('***2233');
  expect((screen.getByLabelText('Çiftçinin cep telefonu') as HTMLInputElement).value).toBe('');
  expect(screen.getByRole('button',{name:'Eşleştirme kodu üret'}).hasAttribute('disabled')).toBe(true);
 });

 it('liste hatası Türkçe metinle gösterilir',async()=>{
  get.mockRejectedValue({response:{status:500,data:{}}});
  mount();
  expect(await screen.findByText('WhatsApp bağlantıları yüklenemedi.')).toBeTruthy();
 });
});

describe('F10-1d — kod penceresinin ömrü',()=>{
 it('kod üret: POST gövdesi, düz kod YALNIZ pencerede, kapanınca DOM\'da yok',async()=>{
  mount();
  await screen.findByText('***2233');
  expect(document.body.textContent).not.toContain(DUZ_KOD);
  fireEvent.click(screen.getByRole('button',{name:'Eşleştirme kodu üret'}));
  const pencere=await screen.findByRole('dialog');
  expect(post).toHaveBeenCalledWith('/whatsapp/party-pairing-codes',
   {party_type:'CUSTOMER',party_id:7,phone:'05321112233'});
  expect(within(pencere).getByTestId('wa-duz-kod').textContent).toBe(DUZ_KOD);
  expect(within(pencere).getByText('KVKK: Rıza çiftçinin ilk mesajıyla alınır.')).toBeTruthy();
  expect(within(pencere).getByText(/\*\*\*1122/)).toBeTruthy();

  fireEvent.click(within(pencere).getByRole('button',{name:'Kapat'}));
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
  expect(document.body.textContent).not.toContain(DUZ_KOD);
  expect(document.body.textContent).not.toContain(DUZ_KOD.replace('-',''));
  // Bekleyen kod satırı kalır — ama düz kodsuz: maskeli hedef + süre.
  const satir=screen.getByTestId('wa-kod-31');
  expect(within(satir).getByText('***1122')).toBeTruthy();
  expect(within(satir).getByText(/dk içinde geçersiz olur/)).toBeTruthy();
 });

 it('Kopyala düz kodu panoya yazar',async()=>{
  const writeText=vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator,'clipboard',{value:{writeText},configurable:true});
  mount();
  await screen.findByText('***2233');
  fireEvent.click(screen.getByRole('button',{name:'Eşleştirme kodu üret'}));
  const pencere=await screen.findByRole('dialog');
  fireEvent.click(within(pencere).getByRole('button',{name:'Kopyala'}));
  await within(pencere).findByRole('button',{name:'Kopyalandı'});
  expect(writeText).toHaveBeenCalledWith(DUZ_KOD);
 });

 it('İptal bekleyen kodu DELETE eder ve satırı kaldırır',async()=>{
  mount();
  await screen.findByText('***2233');
  fireEvent.click(screen.getByRole('button',{name:'Eşleştirme kodu üret'}));
  const pencere=await screen.findByRole('dialog');
  fireEvent.click(within(pencere).getByRole('button',{name:'Kapat'}));
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
  fireEvent.click(screen.getByRole('button',{name:'***1122 kodunu iptal et'}));
  await waitFor(()=>expect(screen.queryByTestId('wa-kod-31')).toBeNull());
  expect(del).toHaveBeenCalledWith('/whatsapp/party-pairing-codes/31');
 });

 it('409 TARAF_NUMARA_PERSONEL sunucunun Türkçe mesajıyla gösterilir, pencere açılmaz',async()=>{
  post.mockRejectedValue({response:{status:409,data:{detail:{
   code:'TARAF_NUMARA_PERSONEL',
   message:'Bu numara bu firmada bir personel hesabına bağlı; önce o bağlantı kapatılmalı.',
  }}}});
  mount();
  await screen.findByText('***2233');
  fireEvent.click(screen.getByRole('button',{name:'Eşleştirme kodu üret'}));
  expect(await screen.findByText(/personel hesabına bağlı/)).toBeTruthy();
  expect(screen.queryByRole('dialog')).toBeNull();
 });
});

describe('F10-1d — bağlantı kapatma',()=>{
 it('onay penceresi: Vazgeç DELETE atmaz',async()=>{
  mount();
  await screen.findByText('***2233');
  fireEvent.click(screen.getByRole('button',{name:'***2233 bağlantısını kapat'}));
  const pencere=await screen.findByRole('dialog');
  fireEvent.click(within(pencere).getByRole('button',{name:'Vazgeç'}));
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
  expect(del).not.toHaveBeenCalled();
 });

 it('onaydan sonra DELETE ve liste yeniden okunur',async()=>{
  mount();
  await screen.findByText('***2233');
  fireEvent.click(screen.getByRole('button',{name:'***2233 bağlantısını kapat'}));
  const pencere=await screen.findByRole('dialog');
  expect(del).not.toHaveBeenCalled();
  linkler=[{...AKTIF,is_active:false},RIZASIZ];
  fireEvent.click(within(pencere).getByRole('button',{name:'Kapat'}));
  await waitFor(()=>expect(screen.queryByText('***2233')).toBeNull());
  expect(del).toHaveBeenCalledWith('/whatsapp/party-links/11');
  expect(get).toHaveBeenCalledTimes(2);
  expect(screen.getByText('***4455')).toBeTruthy();
 });
});
