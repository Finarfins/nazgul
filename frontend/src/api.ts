import axios, {type AxiosError, type InternalAxiosRequestConfig} from 'axios';
import {isAnonymousPath} from './publicPaths';

type RetryableRequest = InternalAxiosRequestConfig & {_retry?: boolean};

/**
 * API kökü. Webde GÖRELİ kalır: site ve API aynı origin'de olduğu için '/api'
 * doğru adrestir ve çerez oturumu ancak böyle çalışır.
 *
 * Mobil (Capacitor) kabuğunda ise uygulamanın origin'i cihazın kendisidir
 * (`https://localhost`); göreli adres telefonun içinde bir sunucu arar ve
 * hiçbir isteği bulamaz. Bu yüzden mutlak adres ŞART.
 *
 * Adres derleme anında `VITE_API_BASE_URL` ile verilir, koda GÖMÜLMEZ:
 * sunucu ve alan adı değişecek (yeni marka), gömülü olsaydı her taşınmada
 * kaynak kodda adres avına çıkmak gerekirdi.
 */
const API_BASE_URL=(import.meta.env.VITE_API_BASE_URL as string|undefined)?.replace(/\/+$/,'')||'/api';
const CSRF_COOKIE='yhp_csrf_token';
const UNSAFE_METHODS=new Set(['post','put','patch','delete']);
const VALIDATION_FIELD_LABELS:Record<string,string>={
 username:'kullanıcı adı',
 password:'şifre',
 current_password:'mevcut şifre',
 new_password:'yeni şifre',
 name:'ad',
 code:'kod',
 barcode:'barkod',
 email:'e-posta',
 company_name:'firma adı',
 display_name:'ad soyad',
 phone:'telefon',
 tax_number:'vergi numarası',
 product_id:'ürün',
 customer_id:'müşteri',
 supplier_id:'tedarikçi',
 warehouse_id:'depo',
 quantity:'miktar',
 unit_price:'birim fiyat',
 discount_percent:'iskonto',
 payment_type:'ödeme yöntemi',
 payment_method:'ödeme yöntemi',
 transaction_date:'belge tarihi',
 due_date:'vade tarihi',
 note:'not',
 items:'satırlar',
};
// Alan adı hassas bir kalıp içeriyorsa değeri hata mesajına koyma — şifre/token/gizli
// anahtar sızıntısını önler (einvoice_password, api_key gibi tam listede olmayanlar dahil).
const SENSITIVE_FIELD_PATTERN=/password|secret|token|api[_-]?key|credential/i;
const VALIDATION_LOC_ROOTS=new Set(['body','query','path','header','cookie']);
type ValidationIssue={type?:unknown;loc?:unknown;msg?:unknown;input?:unknown;ctx?:Record<string,unknown>};

const titleCase=(value:string)=>value?value[0].toLocaleUpperCase('tr-TR')+value.slice(1):value;
const validationField=(key:string)=>VALIDATION_FIELD_LABELS[key]||key.replaceAll('_',' ');
const validationInput=(input:unknown,fieldKey:string)=>{
 if(input===null||input===undefined||SENSITIVE_FIELD_PATTERN.test(fieldKey)||typeof input==='object')return '';
 const value=String(input).trim();
 if(!value)return '';
 const safeValue=value.length>40?`${value.slice(0,37)}…`:value;
 return ` (${safeValue})`;
};
const validationLimit=(value:unknown,fieldLabel:string)=>{
 const normalized=String(value??'').trim();
 return fieldLabel==='iskonto'&&normalized?`%${normalized}`:normalized;
};
const validationPhrase=(issue:ValidationIssue,fieldLabel:string)=>{
 const type=String(issue.type||'');
 const rawMessage=String(issue.msg||'').trim();
 const ctx=issue.ctx||{};
 const contextValue=(key:string)=>ctx[key]??rawMessage.match(/-?\d+(?:\.\d+)?/)?.[0]??'';
 switch(type){
  case 'missing':return 'zorunlu';
  case 'less_than_equal':return `en fazla ${validationLimit(contextValue('le'),fieldLabel)} olabilir`;
  case 'greater_than_equal':return `en az ${validationLimit(contextValue('ge'),fieldLabel)} olabilir`;
  case 'less_than':return `${validationLimit(contextValue('lt'),fieldLabel)} değerinden küçük olmalı`;
  case 'greater_than':return `${validationLimit(contextValue('gt'),fieldLabel)} değerinden büyük olmalı`;
  case 'decimal_parsing':
  case 'decimal_type':
  case 'float_parsing':
  case 'float_type':
  case 'int_parsing':
  case 'int_type':
   return 'geçerli bir sayı olmalı';
  case 'string_type':return 'metin olmalı';
  case 'string_too_short':return `en az ${contextValue('min_length')} karakter olmalı`;
  case 'string_too_long':return `en fazla ${contextValue('max_length')} karakter olmalı`;
  case 'date_from_datetime_parsing':
  case 'date_parsing':
  case 'datetime_from_date_parsing':
  case 'datetime_parsing':
   return 'geçerli bir tarih olmalı';
  case 'bool_parsing':
  case 'bool_type':
   return 'evet veya hayır değeri olmalı';
  case 'enum':
  case 'literal_error':
   return 'izin verilen değerlerden biri olmalı';
  case 'list_type':return 'liste olmalı';
 }
 if(rawMessage.startsWith('Value error, '))return rawMessage.slice('Value error, '.length);
 if(/[çğıöşüÇĞİÖŞÜ]/.test(rawMessage)||/(olmalı|zorunlu|geçersiz|bulunamadı)/i.test(rawMessage))return rawMessage;
 return 'geçersiz değer';
};
const validationMessage=(item:unknown)=>{
 if(typeof item!=='object'||!item)return String(item);
 const issue=item as ValidationIssue;
 const loc=Array.isArray(issue.loc)?issue.loc.filter(part=>!VALIDATION_LOC_ROOTS.has(String(part))):[];
 const rowIndex=loc.find(part=>typeof part==='number');
 const fieldKey=String([...loc].reverse().find(part=>typeof part==='string'&&part!=='items'&&part!=='__root__')||'');
 const fieldLabel=validationField(fieldKey||'değer');
 const input=validationInput(issue.input,fieldKey);
 const phrase=validationPhrase(issue,fieldLabel);
 if(typeof rowIndex==='number')return `${rowIndex+1}. satır${input}: ${fieldLabel} ${phrase}`;
 if(fieldKey)return `${titleCase(fieldLabel)}${input}: ${phrase}`;
 return titleCase(phrase);
};

export const readCookie=(name:string)=>{
 const prefix=`${encodeURIComponent(name)}=`;
 const entry=document.cookie.split(';').map(value=>value.trim()).find(value=>value.startsWith(prefix));
 return entry?decodeURIComponent(entry.slice(prefix.length)):null;
};

const csrfHeaders=()=>{
 const token=readCookie(CSRF_COOKIE);
 return token?{'X-CSRF-Token':token}:{};
};

// FastAPI hata gövdesini her zaman okunabilir metne çevirir. 422 doğrulama
// hatalarında `detail` bir nesne listesidir ({type, loc, msg, ...}); bunu
// doğrudan render etmek React'i çökertir (error #31).
export const errorDetail=(err:unknown,fallback:string):string=>{
 const response=(err as {response?:{status?:number;data?:{detail?:unknown}}})?.response;
 const detail=response?.data?.detail;
 if(typeof detail==='string'&&detail)return detail;
 if(Array.isArray(detail)){
  const messages=detail.map(item=>response?.status===422?validationMessage(item):typeof item==='object'&&item&&'msg' in item?String((item as {msg:unknown}).msg):String(item)).filter(Boolean);
  if(messages.length)return messages.join(' ');
 }
 return fallback;
};

// Ağ hatalarını ve HTTP durum kodlarını kullanıcıya gösterilebilir Türkçe
// metne çevirir; axios'un ham "Request failed with status code 500" metni
// hiçbir zaman kullanıcıya sızmamalıdır.
const statusFallback=(error:AxiosError):string=>{
 if(!error.response)return 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edin.';
 const status=error.response.status;
 if(status>=500)return 'Sunucu hatası. Lütfen tekrar deneyin.';
 if(status===403)return 'Bu işlem için yetkiniz yok.';
 if(status===404)return 'Kayıt bulunamadı.';
 if(status===401)return 'Oturum açmanız gerekiyor.';
 return error.message;
};

// error.message'ı backend'in `detail` mesajıyla (yoksa duruma uygun Türkçe
// fallback ile) değiştirir; `e.message` gösteren her tüketici otomatik düzelir.
// Ayrıca `detail` bir 422 doğrulama dizisi (nesne listesi) geldiğinde onu da
// aynı okunabilir metinle DEĞİŞTİRİR: sayfaların yaygın
// `setError(e.response?.data?.detail||'...')` deseni ham diziyi React child
// olarak render edip uygulamayı çökertiyordu (React #31).
export const unwrapApiError=(error:unknown):unknown=>{
 if(axios.isAxiosError(error)){
  const message=errorDetail(error,statusFallback(error));
  error.message=message;
  const data=error.response?.data as {detail?:unknown}|undefined;
  if(data&&data.detail!==undefined&&typeof data.detail!=='string')data.detail=message;
 }
 return error;
};

export const api=axios.create({baseURL:API_BASE_URL,timeout:15000,withCredentials:true});
const refreshClient=axios.create({baseURL:API_BASE_URL,timeout:15000,withCredentials:true});
let refreshPromise:Promise<void>|null=null;

api.interceptors.request.use((config)=>{
 try{
  const company=JSON.parse(localStorage.getItem('yhp_company')||'null');
  if(company?.id)config.headers['X-Company-ID']=String(company.id);
 }catch{/* ignore */}
 const method=String(config.method||'get').toLowerCase();
 const csrf=readCookie(CSRF_COOKIE);
 if(UNSAFE_METHODS.has(method)&&csrf)config.headers['X-CSRF-Token']=csrf;
 return config;
});

const expireBrowserSession=()=>{
 // Clean up credentials persisted by pre-hardening builds. Current versions
 // never write access tokens or user records to Web Storage.
 localStorage.removeItem('yhp_token');
 localStorage.removeItem('yhp_user');
 window.dispatchEvent(new Event('yhp:auth-expired'));
 // Oturum gerektirmeyen sayfalarda YÖNLENDİRME YAPMA. Anonim ziyaretçi
 // /tanitim, /kayit veya /eposta-dogrula açtığında da ilk /auth/me çağrısı 401
 // döner ve buraya düşerdi; eskiden koşul yalnız "zaten /giris'te miyim" olduğu
 // için ziyaretçi giriş ekranına fırlatılıyordu. Sonuç: tanıtım sayfası
 // açılmıyor, self-servis kayıt ve e-postadaki doğrulama bağlantısı
 // çalışmıyordu.
 //
 // Ölçüt ANONYMOUS_PATHS'tir, PUBLIC_PATHS DEĞİL: /sifre-degistir kabuk dışıdır
 // ama oturum ister. Onu da muaf tutsaydık oturumu biten kullanıcı o ekranda
 // takılı kalırdı — istekler 401 döner, parola değişmez, giriş ekranına da
 // atılmazdı.
 if(!isAnonymousPath(location.pathname))location.href='/giris';
};

api.interceptors.response.use(
 response=>response,
 async(error:AxiosError)=>{
  const status=error.response?.status;
  const config=error.config as RetryableRequest|undefined;
  const url=String(config?.url||'');
  const refreshable=status===401&&config&&!config._retry&&!url.includes('/auth/login')&&!url.includes('/auth/refresh');
  if(!refreshable)return Promise.reject(unwrapApiError(error));
  config._retry=true;
  try{
   if(!refreshPromise){
    refreshPromise=refreshClient.post('/auth/refresh',{}, {headers:csrfHeaders()}).then(()=>undefined).finally(()=>{refreshPromise=null});
   }
   await refreshPromise;
   return api.request(config);
  }catch(refreshError){
   expireBrowserSession();
   return Promise.reject(unwrapApiError(refreshError));
  }
 }
);

export const money=(value:number|string|null|undefined)=>new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY'}).format(Number(value||0));
function presentBlob(data:Blob,filename?:string){
 const href=URL.createObjectURL(data);
 const a=document.createElement('a');a.href=href;a.target='_blank';if(filename)a.download=filename;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),30000);
}
export async function openAuthenticated(url:string,filename?:string){
 const response=await api.get(url,{responseType:'blob'});
 presentBlob(response.data,filename);
}
/** Gövde gerektiren çıktı uçları için (ör. toplu etiket PDF'i). */
export async function postAuthenticated(url:string,body:unknown,filename?:string){
 const response=await api.post(url,body,{responseType:'blob'});
 presentBlob(response.data,filename);
}

export const policyOverrideHeaders=(reason:string)=>({
 'X-Policy-Override':'true',
 'X-Policy-Override-Reason':encodeURIComponent(reason.trim()),
});
export const policyOverrideRequired=(error:any)=>{
 const detail=String(error?.response?.data?.detail||'');
 return error?.response?.status===409&&detail.includes('Yönetici onayıyla');
};
