import {describe,expect,it} from 'vitest';

import {
 bordroSatirNo,DURUMLAR,EYLEMLER,eylemEtkinMi,eylemGorunurMu,GECISLER,gunEkle,hataMesaji,izinliMi,kurusToplami,
 MUHASEBE_HEDEFLERI,MUHASEBE_ROLLERI,rolHedefeGidebilirMi,
 type Durum,type EylemAnahtari,
} from './cekSenet';

/**
 * Backend `cek_senet_engine.GECISLER`in ELLE kopyası — türetilmedi, yoksa
 * test kendi kendini doğrulardı. 36 çiftten tam 7'si izinli.
 */
const BEKLENEN:Record<Durum,Durum[]>={
 portfoyde:['tahsile_verildi','ciro_edildi','iade'],
 tahsile_verildi:['tahsil_edildi','karsiliksiz','portfoyde'],
 karsiliksiz:['iade'],
 tahsil_edildi:[],
 ciro_edildi:[],
 iade:[],
};

/** Durum -> ETKİN eylemler, alınan ve verilen evrak için. Ciro yalnız alınanda. */
const ETKIN:Record<Durum,{alinan:EylemAnahtari[];verilen:EylemAnahtari[]}>={
 portfoyde:{alinan:['tahsile_ver','ciro','iade'],verilen:['tahsile_ver','iade']},
 tahsile_verildi:{alinan:['tahsil','karsiliksiz','portfoye_geri'],verilen:['tahsil','karsiliksiz','portfoye_geri']},
 karsiliksiz:{alinan:['iade'],verilen:['iade']},
 tahsil_edildi:{alinan:[],verilen:[]},
 ciro_edildi:{alinan:[],verilen:[]},
 iade:{alinan:[],verilen:[]},
};

describe('çek/senet durum makinesi aynası',()=>{
 it('6x6 = 36 çiftin tam 7si izinli ve backend tablosuyla birebir',()=>{
  let izinli=0;
  for(const kaynak of DURUMLAR)for(const hedef of DURUMLAR){
   const beklenen=BEKLENEN[kaynak].includes(hedef);
   expect([kaynak,hedef,izinliMi(kaynak,hedef)]).toEqual([kaynak,hedef,beklenen]);
   if(beklenen)izinli++;
  }
  expect(izinli).toBe(7);
  expect(Object.keys(GECISLER).sort()).toEqual([...DURUMLAR].sort());
 });

 it('bilinmeyen kaynak durum hiçbir geçişe izin vermez',()=>{
  for(const hedef of DURUMLAR)expect(izinliMi('bilinmeyen',hedef)).toBe(false);
 });

 it('yedi geçiş altı eyleme düşer; her eylem tek bir hedefe gider',()=>{
  expect(EYLEMLER).toHaveLength(6);
  expect(new Set(EYLEMLER.map(e=>e.hedef)).size).toBe(6);
 });

 for(const durum of DURUMLAR){
  for(const yon of ['alinan','verilen'] as const){
   it(`${durum} / ${yon}: etkin eylemler ${ETKIN[durum][yon].join(', ')||'(yok)'}`,()=>{
    const evrak={portfoy_durumu:durum,yon};
    const etkin=EYLEMLER.filter(e=>eylemEtkinMi(e,evrak)).map(e=>e.anahtar);
    expect(etkin).toEqual(ETKIN[durum][yon]);
   });
  }
 }

 it('Ciro Et verilen evrakta HİÇ görünmez, alınan evrakta görünür',()=>{
  const ciro=EYLEMLER.find(e=>e.anahtar==='ciro')!;
  expect(eylemGorunurMu(ciro,{yon:'verilen'})).toBe(false);
  expect(eylemGorunurMu(ciro,{yon:'alinan'})).toBe(true);
  for(const eylem of EYLEMLER.filter(e=>e.anahtar!=='ciro'))expect(eylemGorunurMu(eylem,{yon:'verilen'})).toBe(true);
 });
});

/**
 * Backend `cek_senet_engine.rol_hedefe_gidebilir_mi` doğruluk tablosunun ELLE
 * kopyası (H48): hedef -> rol -> gidebilir mi. Türetilmedi.
 */
const ROL_TABLOSU:Record<Durum,Record<'admin'|'yonetici'|'muhasebe'|'satis',boolean>>={
 portfoyde:{admin:true,yonetici:true,muhasebe:true,satis:true},
 tahsile_verildi:{admin:true,yonetici:true,muhasebe:true,satis:true},
 tahsil_edildi:{admin:true,yonetici:true,muhasebe:true,satis:true},
 ciro_edildi:{admin:true,yonetici:true,muhasebe:true,satis:false},
 karsiliksiz:{admin:true,yonetici:true,muhasebe:true,satis:false},
 iade:{admin:true,yonetici:true,muhasebe:true,satis:false},
};

describe('rol kapısı aynası (H50)',()=>{
 it('sabitler backend MUHASEBE_HEDEFLERI / MUHASEBE_ROLLERI ile birebir',()=>{
  expect([...MUHASEBE_HEDEFLERI].sort()).toEqual(['ciro_edildi','iade','karsiliksiz']);
  expect([...MUHASEBE_ROLLERI].sort()).toEqual(['admin','muhasebe','yonetici']);
 });
 for(const hedef of DURUMLAR){
  for(const [rol,beklenen] of Object.entries(ROL_TABLOSU[hedef])){
   it(`${rol} -> ${hedef}: ${beklenen}`,()=>{
    expect(rolHedefeGidebilirMi(rol,hedef)).toBe(beklenen);
   });
  }
 }
 it('bilinmeyen ya da boş rol riskli hedeflere gidemez, diğerlerine gidebilir',()=>{
  for(const rol of ['','depo','rapor','ADMIN']){
   expect(DURUMLAR.filter(h=>rolHedefeGidebilirMi(rol,h))).toEqual(['portfoyde','tahsile_verildi','tahsil_edildi']);
  }
 });
});

describe('yardımcılar',()=>{
 it('kuruş toplamı kayan nokta kaydırmaz',()=>{
  expect(kurusToplami(['0.10','0.20'])).toBe(0.3);
  expect(kurusToplami(['1250.55','999.45',3])).toBe(2253);
  expect(kurusToplami([])).toBe(0);
 });

 it('gün ekleme ay ve yıl sınırını geçer',()=>{
  expect(gunEkle('2026-12-28',7)).toBe('2027-01-04');
  expect(gunEkle('2026-03-01',-1)).toBe('2026-02-28');
 });

 it('bordro hata mesajından satır numarası okunur',()=>{
  expect(bordroSatirNo('3. satır: seri no zorunlu')).toBe(3);
  expect(bordroSatirNo('Satır 12: Müşteri bulunamadı')).toBe(12);
  expect(bordroSatirNo('Sunucu hatası')).toBeNull();
 });

 it('hata mesajı: metin detay, kodlu nesne, yedek',()=>{
  expect(hataMesaji({response:{data:{detail:'Tahsil hesabı bulunamadı'}}},'y')).toBe('Tahsil hesabı bulunamadı');
  expect(hataMesaji({response:{data:{detail:{code:'CEK_GECIS_GECERSIZ',message:'İzin verilmeyen portföy geçişi'}}}},'y')).toBe('İzin verilmeyen portföy geçişi');
  expect(hataMesaji({},'yedek')).toBe('yedek');
 });
});
