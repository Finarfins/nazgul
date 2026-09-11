/**
 * Çek/senet portföyü — istemci tarafı sabitler ve durum makinesinin AYNASI.
 *
 * Geçiş tablosunun TEK doğru kaynağı backend'dedir
 * (`backend/app/cek_senet_engine.py::GECISLER`). Buradaki kopya yalnız
 * düğmeleri önceden devre dışı bırakmak içindir; sunucu yine 409
 * (`CEK_GECIS_GECERSIZ` / `CEK_CIRO_YALNIZ_ALINAN`) döndürebilir ve sayfa o
 * mesajı gösterir. 6x6 = 36 çiftten 7'si izinli — `cekSenet.test.ts` tabloyu
 * altı durumun her biri için ayrı ayrı sabitler.
 */
import type {components} from '../../api/types.gen';

export type CekSenet=components['schemas']['CekSenet'];
export type CekSenetGirdisi=components['schemas']['CekSenetGirdisi'];
export type CekSenetListesi=components['schemas']['CekSenetListesi'];
export type BordroSonucu=components['schemas']['BordroSonucu'];

export type Durum='portfoyde'|'tahsile_verildi'|'tahsil_edildi'|'ciro_edildi'|'karsiliksiz'|'iade';
export const DURUMLAR:readonly Durum[]=['portfoyde','tahsile_verildi','tahsil_edildi','ciro_edildi','karsiliksiz','iade'];

export const DURUM_ETIKETI:Record<Durum,string>={
 portfoyde:'Portföyde',
 tahsile_verildi:'Tahsilde',
 tahsil_edildi:'Tahsil Edildi',
 ciro_edildi:'Ciro Edildi',
 karsiliksiz:'Karşılıksız',
 iade:'İade',
};

type ChipRengi='default'|'primary'|'secondary'|'error'|'info'|'success'|'warning';
export const DURUM_RENGI:Record<Durum,ChipRengi>={
 portfoyde:'primary',
 tahsile_verildi:'info',
 tahsil_edildi:'success',
 ciro_edildi:'secondary',
 karsiliksiz:'error',
 iade:'default',
};

export const TUR_ETIKETI:Record<string,string>={cek:'Çek',senet:'Senet'};
export const YON_ETIKETI:Record<string,string>={alinan:'Alınan',verilen:'Verilen'};

/** Kaynak -> gidilebilecek hedefler. Backend `GECISLER` ile birebir. */
export const GECISLER:Record<Durum,readonly Durum[]>={
 portfoyde:['tahsile_verildi','ciro_edildi','iade'],
 tahsile_verildi:['tahsil_edildi','karsiliksiz','portfoyde'],
 karsiliksiz:['iade'],
 tahsil_edildi:[],
 ciro_edildi:[],
 iade:[],
};

/** Hâlâ elde/bankada olan, yani vadesi takip edilen durumlar. */
export const ACIK_DURUMLAR:readonly Durum[]=['portfoyde','tahsile_verildi'];

export type EylemAnahtari='tahsile_ver'|'tahsil'|'ciro'|'karsiliksiz'|'iade'|'portfoye_geri';
export type Eylem={anahtar:EylemAnahtari;hedef:Durum;etiket:string;baslik:string};

/**
 * Satır eylemleri. Her eylem TAM OLARAK bir `durum-degistir` çağrısıdır.
 * `iade` iki kaynaktan (portföyde, karşılıksız) gidilebilen tek hedeftir; bu
 * yüzden yedi geçiş altı eyleme düşer.
 */
export const EYLEMLER:readonly Eylem[]=[
 {anahtar:'tahsile_ver',hedef:'tahsile_verildi',etiket:'Tahsile Ver',baslik:'Bankaya Tahsile Ver'},
 {anahtar:'tahsil',hedef:'tahsil_edildi',etiket:'Bankadan Tahsil',baslik:'Bankadan Tahsil Kaydı'},
 {anahtar:'ciro',hedef:'ciro_edildi',etiket:'Ciro Et',baslik:'Tedarikçiye Ciro Et'},
 {anahtar:'karsiliksiz',hedef:'karsiliksiz',etiket:'Karşılıksız',baslik:'Karşılıksız İşaretle'},
 {anahtar:'iade',hedef:'iade',etiket:'İade',baslik:'Cariye İade Et'},
 {anahtar:'portfoye_geri',hedef:'portfoyde',etiket:'Portföye Geri Al',baslik:'Portföye Geri Al'},
];

export const izinliMi=(kaynak:string,hedef:Durum)=>(GECISLER[kaynak as Durum]||[]).includes(hedef);

/** Ciro yalnız ALINAN evrakta vardır: verilen evrakta eylem hiç gösterilmez. */
export const eylemGorunurMu=(eylem:Eylem,evrak:Pick<CekSenet,'yon'>)=>eylem.hedef!=='ciro_edildi'||evrak.yon==='alinan';

export const eylemEtkinMi=(eylem:Eylem,evrak:Pick<CekSenet,'yon'|'portfoy_durumu'>)=>
 eylemGorunurMu(eylem,evrak)&&izinliMi(evrak.portfoy_durumu,eylem.hedef);

/** Yerel takvim günü (YYYY-MM-DD). `toISOString` UTC'dir ve gece yarısı
 *  civarında bir önceki/sonraki güne kayar; vade takvimi yerel güne bakar. */
export const yerelTarih=(tarih:Date=new Date())=>{
 const yil=tarih.getFullYear();
 const ay=String(tarih.getMonth()+1).padStart(2,'0');
 const gun=String(tarih.getDate()).padStart(2,'0');
 return `${yil}-${ay}-${gun}`;
};

export const gunEkle=(tarih:string,gun:number)=>{
 const [y,a,g]=tarih.split('-').map(Number);
 return yerelTarih(new Date(y,a-1,g+gun));
};

/** Tutarlar sunucudan Decimal METNİ olarak gelir. Toplam kuruş tamsayısında
 *  alınır; kayan noktalı toplama kuruş kaydırmaz. */
export const kurusToplami=(tutarlar:readonly (string|number)[])=>
 tutarlar.reduce<number>((toplam,tutar)=>toplam+Math.round(Number(tutar)*100),0)/100;

/** Sunucu hata mesajı. `api.ts` önleyicisi `detail`i metne çevirir; önleyici
 *  devrede değilken (test) nesne `{code,message}` biçimi de okunur. */
export const hataMesaji=(hata:unknown,yedek:string):string=>{
 const yanit=(hata as {response?:{data?:{detail?:unknown}}})?.response;
 const detay=yanit?.data?.detail;
 if(typeof detay==='string'&&detay)return detay;
 if(detay&&typeof detay==='object'&&typeof (detay as {message?:unknown}).message==='string')return (detay as {message:string}).message;
 const mesaj=(hata as {message?:unknown})?.message;
 return typeof mesaj==='string'&&mesaj?mesaj:yedek;
};

/** Bordro hatasından 1-tabanlı satır numarası: şema hatası `N. satır`,
 *  taraf doğrulaması `Satır N:` biçimindedir. */
export const bordroSatirNo=(mesaj:string):number|null=>{
 const eslesme=mesaj.match(/^(\d+)\. satır/)||mesaj.match(/^Satır (\d+):/);
 return eslesme?Number(eslesme[1]):null;
};
