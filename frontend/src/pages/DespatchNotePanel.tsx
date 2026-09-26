import {useCallback,useEffect,useMemo,useState} from 'react';
import {
 Accordion,AccordionDetails,AccordionSummary,Alert,Box,Button,Chip,CircularProgress,
 Dialog,DialogActions,DialogContent,DialogTitle,Paper,Snackbar,Stack,Table,TableBody,
 TableCell,TableHead,TableRow,TextField,Tooltip,Typography,
} from '@mui/material';
import CallSplitIcon from '@mui/icons-material/CallSplit';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import SendIcon from '@mui/icons-material/Send';
import SyncIcon from '@mui/icons-material/Sync';
import {api,apiDetail,errorDetail} from '../api';
import type {components} from '../api/types.gen';
import {useAuth} from '../AuthContext';
import {decimalPayload,quantityDecimal} from '../utils/documentMoney';
import {formatDate} from './ReceivablesAging';

// e-İrsaliye durumları — `app/einvoice/edespatch.py::BILINEN` ile BİREBİR
// aynı ON BİR değer. e-Fatura'nın (`EInvoiceStatus`) sözlüğünden AYRIDIR ve
// bu kasıtlı: aynı sayı iki üründe farklı şey demek (keşif §1/§5, `100`).
// AYRIŞMA BİR HATADIR ve ÖLÇÜLÜYOR: şema `edespatch_status`ı düz `string`
// olarak üretiyor (`types.gen.ts`te numaralandırma YOK), o yüzden test ON
// BİR ADI ÇAKIYOR ve `STATUS_VIEW`in anahtar kümesini ona eşitliyor.
export type EDespatchStatus=
 'NONE'|'QUEUED'|'PROCESSING'|'SIGNED'|'SENT'|'DELIVERED'|'FAILED'|'UNKNOWN'
 |'ACCEPTED'|'PARTIALLY_ACCEPTED'|'REJECTED';

// Birliğin ÇALIŞMA ZAMANINDAKİ karşılığı. Tip tek başına test edilemez
// (derlemede silinir); bu dizi edilebilir ve `STATUS_VIEW`in anahtar
// kümesiyle eşitliği bir kapıdır.
export const EDESPATCH_STATUSES:readonly EDespatchStatus[]=[
 'NONE','QUEUED','PROCESSING','SIGNED','SENT','DELIVERED','FAILED','UNKNOWN',
 'ACCEPTED','PARTIALLY_ACCEPTED','REJECTED',
];

// Ticari yanıt özeti (`despatch_notes.response_status`) —
// `edespatch.py::YANIT_TURLERI` ile AYNI üç değer.
export type ResponseStatus='KABUL'|'RED'|'KISMI_KABUL';

export type DespatchNote={
 id:number;
 invoice_id:number;
 despatch_uuid:string;
 despatch_number:string|null;
 issue_date?:string|null;
 edespatch_status:EDespatchStatus;
 edespatch_gib_status_code:string|null;
 edespatch_provider_uuid:string|null;
 edespatch_last_error:string|null;
 response_status?:ResponseStatus|null;
 driver_name:string|null;
 vehicle_plate:string|null;
 // YALNIZ detay ucunda (`GET /despatch-notes/{id}`) ve oluşturma yanıtında
 // dolu gelir; liste ucu satır TAŞIMAZ (sunucudaki N+1 gerekçesi).
 lines?:{line_no:number;item_name:string;quantity:string}[];
};

export type DespatchableItem={
 invoice_item_id:number;
 invoice_line_no:number;
 description:string|null;
 product_id:number|null;
 invoiced:string;
 despatched:string;
 remaining:string;
};

// H88: yanıt görünümü ÜRETİLEN şemadan (`IrsaliyeYanitiGorunumu`) türer; elle
// yazılmış bir kopya sunucudan AYRIŞIRSA `tsc -b` onu artık yakalar. Yalnız
// şemanın düz `string` ürettiği iki durum alanı buradaki birliklere daraltılır
// (numaralandırma şemada YOK — `EDespatchStatus` notu).
type UretilenYanit=components['schemas']['IrsaliyeYanitiGorunumu'];
type UretilenYanitBelgesi=components['schemas']['IrsaliyeYanitBelgesi'];

export type DespatchResponseLine=components['schemas']['IrsaliyeYanitSatiri'];

export type DespatchResponseView=
 Omit<UretilenYanit,'edespatch_status'|'response_status'|'response'>&{
  edespatch_status:EDespatchStatus;
  response_status:ResponseStatus|null;
  response:(Omit<UretilenYanitBelgesi,'response_type'>&{response_type:ResponseStatus})|null;
 };

type ChipColor='default'|'info'|'success'|'error'|'warning';

export const STATUS_VIEW:Record<EDespatchStatus,{label:string;color:ChipColor}>={
 NONE:{label:'e-İrsaliye: gönderilmedi',color:'default'},
 QUEUED:{label:'e-İrsaliye: kuyrukta',color:'info'},
 PROCESSING:{label:'e-İrsaliye: işleniyor',color:'info'},
 SIGNED:{label:'e-İrsaliye: imzalandı',color:'info'},
 SENT:{label:'e-İrsaliye: gönderildi',color:'info'},
 // E4b-2: TESLİM artık SON DURAK DEĞİL. Alıcının ticari yanıtı bekleniyor
 // ve etiket bunu SÖYLÜYOR; eski "alındı", kabul edilmiş gibi okunurdu.
 DELIVERED:{label:'e-İrsaliye: teslim edildi — yanıt bekleniyor',color:'info'},
 FAILED:{label:'e-İrsaliye: başarısız',color:'error'},
 // UNKNOWN `error` DEĞİL `warning`: bir hata değil, bir BELİRSİZLİK.
 // Kırmızı göstermek kullanıcıyı "yeniden gönder"e iter ve tam olarak o
 // yol kapalıdır (çift irsaliye riski) — doğru eylem SORGULAMAKTIR.
 UNKNOWN:{label:'e-İrsaliye: durum bilinmiyor',color:'warning'},
 // E4b-2 terminalleri (`edespatch.YANIT_DURUMLARI`). KISMİ KABUL `warning`:
 // iş BİTTİ ama EKSİK bitti — yeşil göstermek reddedilen satırları
 // görünmez kılardı, kırmızı ise alınan malı yok sayardı.
 ACCEPTED:{label:'e-İrsaliye: kabul edildi',color:'success'},
 PARTIALLY_ACCEPTED:{label:'e-İrsaliye: kısmen kabul edildi',color:'warning'},
 REJECTED:{label:'e-İrsaliye: reddedildi',color:'error'},
};

const RESPONSE_VIEW:Record<ResponseStatus,{label:string;color:ChipColor}>={
 KABUL:{label:'Kabul',color:'success'},
 RED:{label:'Red',color:'error'},
 KISMI_KABUL:{label:'Kısmi kabul',color:'warning'},
};

// Gönderim YALNIZ bu iki durumdan açıktır. Sunucudaki
// `edespatch.GONDERIM_KAPALI`nın tümleyeni; buton onunla AYNI kuralı
// gösterir ki kullanıcı 409 alan bir butona basmasın.
const GONDERILEBILIR:ReadonlySet<string>=new Set(['NONE','FAILED']);

// Yanıt bölmesinin AÇILDIĞI durumlar: teslim edilmiş bir belgenin yanıtı
// ya VARDIR ya BEKLENİYORDUR (zımni kabul sayacı da orada gösterilir).
// Öncesinde sorulacak bir şey yok — boş bir bölme gürültüdür.
const YANIT_GORUNUR:ReadonlySet<string>=
 new Set(['DELIVERED','ACCEPTED','PARTIALLY_ACCEPTED','REJECTED']);

// `UNKNOWN`da yapılacak TEK iş sorgudur ve arayüz bunu SÖYLER. Sunucu da
// aynı cümleyi 409 gövdesinde veriyor; burada kullanıcı o hatayı ALMADAN
// önce görüyor.
const BELIRSIZ_UYARISI=
 'Önceki gönderimin sonucu bilinmiyor. Çift irsaliye riskine karşı önce '+
 '"Durumu Sorgula" ile sağlayıcıya sorun.';

const TAMAMLANDI_IPUCU='Tüm kalemler sevk edildi';

// Sunucunun adı konmuş 422 kodları (`routers/despatch_notes.py`) →
// SATIRIN ALTINDA gösterilecek Türkçe cümle. İstemci METNE değil KODA
// bakar; sunucu mesajını değiştirse bile bu eşleme kaymaz.
const SATIR_HATALARI:Record<string,string>={
 SEVK_MIKTAR_ASIMI:'Sevk miktarı kalan miktarı aşıyor.',
 HIZMET_SATIRI_SEVK_EDILMEZ:'Hizmet kalemi e-İrsaliye ile sevk edilmez.',
 FATURA_KALEMI_YOK:'Bu kalem faturaya ait değil; listeyi yenileyin.',
 SEVK_SATIRI_TEKRAR:'Aynı fatura kalemi bir irsaliyede iki kez yazılamaz.',
};
const SEVK_KALEMI_YOK_MESAJI=
 'Sevk edilecek kalem seçilmedi; en az bir satıra miktar yazın.';
const TAMAMLANDI_MESAJI='Faturanın bütün mal kalemleri sevk edildi.';

// PROJENİN MİKTAR BİÇİMİ — `utils/documentMoney.quantityDecimal`
// (`PartiMutabakati`nin `miktar`ıyla AYNI): dört hane, float yok.
const miktar=(value:string|number|null|undefined)=>quantityDecimal(value??0).toFixed();

type FormState={
 actual_shipment_at:string;
 driver_name:string;
 driver_national_id:string;
 vehicle_plate:string;
 trailer_plate:string;
 delivery_address:string;
 delivery_postal_code:string;
};

const BOS_FORM:FormState={
 actual_shipment_at:'',driver_name:'',driver_national_id:'',
 vehicle_plate:'',trailer_plate:'',delivery_address:'',delivery_postal_code:'',
};

/** İrsaliyenin ticari yanıtı. İsteğini KENDİ AÇILDIĞINDA yapar — panel her
 * satır için peşin sormaz; görülmeyen bir bölme için istek atmak listeyi
 * N+1'e çevirirdi. `yenile` sayacı `sync` sonrası artar. */
function YanitBolmesi({despatchId,yenile}:{despatchId:number;yenile:number}){
 const [acik,setAcik]=useState(false);
 const [veri,setVeri]=useState<DespatchResponseView|null>(null);
 const [yukleniyor,setYukleniyor]=useState(false);
 const [hata,setHata]=useState('');

 useEffect(()=>{
  if(!acik)return;
  setYukleniyor(true);setHata('');
  api.get(`/despatch-notes/${despatchId}/response`)
   .then(response=>setVeri(response.data as DespatchResponseView))
   .catch(err=>setHata(errorDetail(err,'İrsaliye yanıtı yüklenemedi.')))
   .finally(()=>setYukleniyor(false));
 },[despatchId,acik,yenile]);

 const yanit=veri?.response??null;
 const tur=yanit?RESPONSE_VIEW[yanit.response_type]:null;

 return <Accordion expanded={acik} onChange={(_,value)=>setAcik(value)}
   disableGutters variant="outlined" sx={{mt:1}}>
  <AccordionSummary expandIcon={<ExpandMoreIcon/>}>
   <Typography variant="body2" fontWeight={700}>Yanıt</Typography>
  </AccordionSummary>
  <AccordionDetails>
   {yukleniyor&&<Typography variant="body2" color="text.secondary">
    Yanıt yükleniyor…
   </Typography>}
   {hata&&<Alert severity="error">{hata}</Alert>}
   {veri&&!yukleniyor&&!hata&&<Stack spacing={1}>
    {/* ZIMNİ KABUL YALNIZ YANIT YOKKEN. Yanıt geldikten sonra sayaç
        ANLAMSIZDIR ve gösterilmesi "hâlâ bekleniyor" diye okunurdu. */}
    {!yanit&&veri.implicit_accept_due_at&&
     <Typography variant="body2" color="text.secondary">
      {`Zımni kabul: ${formatDate(veri.implicit_accept_due_at)}`}
     </Typography>}
    {!yanit&&<Typography variant="body2" color="text.secondary">
     Alıcıdan henüz ticari yanıt gelmedi.
    </Typography>}
    {yanit&&tur&&<Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
     <Chip size="small" label={tur.label} color={tur.color}/>
     <Typography variant="body2">{formatDate(yanit.issue_date)}</Typography>
     <Typography variant="body2" color="text.secondary">{yanit.response_number}</Typography>
    </Stack>}
    {yanit?.notes&&<Typography variant="body2">{yanit.notes}</Typography>}
    {/* ETKİLİ YANIT İLK KAYDEDİLENDİR (sunucu: kimlik sırası). Sonrakiler
        saklanır ama durumu DEĞİŞTİRMEZ; sayı onların varlığını söyler. */}
    {veri.responses_count>1&&<Typography variant="caption" color="text.secondary">
     {`${veri.responses_count} yanıt, ilki (geçerli olan) gösteriliyor`}
    </Typography>}
    {veri.lines.length>0&&<Table size="small">
     <TableHead><TableRow>
      <TableCell>Kalem</TableCell>
      <TableCell align="right">Sevk</TableCell>
      <TableCell align="right">Alınan</TableCell>
      <TableCell align="right">Reddedilen</TableCell>
      <TableCell>Gerekçe</TableCell>
     </TableRow></TableHead>
     <TableBody>
      {veri.lines.map(satir=>{
       // REDDEDİLEN SATIR VURGULANIR: bir kısmi kabulde kullanıcının
       // ARADIĞI tek şey odur; tabloda kaybolmamalı.
       const redli=quantityDecimal(satir.rejected_quantity).greaterThan(0);
       return <TableRow key={satir.despatch_line_id}
         data-testid={`yanit-satiri-${satir.line_no}`}
         data-redli={redli?'1':'0'}
         sx={redli?{bgcolor:'error.light'}:undefined}>
        <TableCell>{satir.item_name??'-'}</TableCell>
        <TableCell align="right">{miktar(satir.despatched_quantity)}</TableCell>
        <TableCell align="right">{miktar(satir.received_quantity)}</TableCell>
        <TableCell align="right">{miktar(satir.rejected_quantity)}</TableCell>
        <TableCell>{satir.reject_reason??'-'}</TableCell>
       </TableRow>;
      })}
     </TableBody>
    </Table>}
   </Stack>}
  </AccordionDetails>
 </Accordion>;
}

export function DespatchNotePanel({invoiceId}:{invoiceId:number}){
 const {can}=useAuth();
 const [notes,setNotes]=useState<DespatchNote[]>([]);
 const [items,setItems]=useState<DespatchableItem[]>([]);
 const [tamamlandi,setTamamlandi]=useState(false);
 const [loading,setLoading]=useState(true);
 const [busy,setBusy]=useState(false);
 // `null` kapalı; 'tam' bütün kalanı sevk eder (E4a düz sevki), 'kismi'
 // satır satır miktar sorar (E4b-1). İKİ AYRI DİYALOG DEĞİL: şoför/plaka
 // alanları ikisinde de AYNI ve iki kopya iki kez kaymaya davetiyedir.
 const [dialogMode,setDialogMode]=useState<'tam'|'kismi'|null>(null);
 const [form,setForm]=useState<FormState>(BOS_FORM);
 const [quantities,setQuantities]=useState<Record<number,string>>({});
 const [satirHatalari,setSatirHatalari]=useState<Record<number,string>>({});
 const [dialogHata,setDialogHata]=useState('');
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [toast,setToast]=useState('');
 const [yanitYenile,setYanitYenile]=useState(0);

 // `sessiz`: TAZELEMEDE TAM EKRAN İPLİKÇİK YOK. İlk yüklemede panel henüz
 // boştur ve iplikçik doğru cevaptır; bir eylemden sonra ise paneli
 // iplikçikle değiştirmek az önce basılan uyarıyı da SİLER.
 const load=useCallback((sessiz=false)=>{
  if(!sessiz)setLoading(true);
  setError('');
  // İKİ İSTEK, TEK YÜKLEME: liste irsaliyeleri, `despatchable-items`
  // kalanları verir. Satır SAYISI liste ucunda YOK (sunucudaki N+1
  // gerekçesi), o yüzden her irsaliyenin detayı AYRICA çekilir — sınır
  // FATURA BAŞINA irsaliye sayısıdır, sayfa boyu değil.
  Promise.all([
   api.get(`/despatch-notes?invoice_id=${invoiceId}`),
   api.get(`/invoices/${invoiceId}/despatchable-items`),
  ])
   .then(async([listResponse,itemsResponse])=>{
    const liste=(listResponse.data?.items??[]) as DespatchNote[];
    const detaylar=await Promise.all(liste.map(note=>
     api.get(`/despatch-notes/${note.id}`)
      .then(detay=>({...note,...(detay.data as DespatchNote)}))
      .catch(()=>note)));
    setNotes(detaylar);
    setItems((itemsResponse.data?.items??[]) as DespatchableItem[]);
    setTamamlandi(Boolean(itemsResponse.data?.complete));
   })
   .catch(err=>setError(errorDetail(err,'e-İrsaliye durumu yüklenemedi.')))
   .finally(()=>setLoading(false));
 },[invoiceId]);
 useEffect(load,[load]);

 const alan=(key:keyof FormState)=>(event:{target:{value:string}})=>
  setForm(current=>({...current,[key]:event.target.value}));

 const dialogAc=(mode:'tam'|'kismi')=>{
  setDialogMode(mode);setDialogHata('');setSatirHatalari({});
  // VARSAYILAN KALANDIR: en sık istenen kısmi sevk "geri kalanını da
  // gönder"dir ve kullanıcıya her satıra aynı sayıyı yazdırmak gereksiz.
  setQuantities(Object.fromEntries(items.map(item=>[item.invoice_item_id,
   quantityDecimal(item.remaining).greaterThan(0)?miktar(item.remaining):''])));
 };

 const dialogKapat=()=>{setDialogMode(null);setSatirHatalari({});setDialogHata('')};

 // SIFIR VE BOŞ SATIR GÖNDERİLMEZ: sunucu `quantity > 0` istiyor ve
 // sıfırlık bir satır "bu kalemi sevk etme" demenin yazılışıdır.
 const secilenSatirlar=useMemo(()=>items
  .map(item=>({item,value:quantityDecimal(quantities[item.invoice_item_id]??0)}))
  .filter(({value})=>value.greaterThan(0)),[items,quantities]);

 const create=async()=>{
  const kismiMi=dialogMode==='kismi';
  if(kismiMi&&!secilenSatirlar.length){setDialogHata(SEVK_KALEMI_YOK_MESAJI);return}
  setBusy(true);setDialogHata('');setSatirHatalari({});setError('');setNotice('');
  try{
   const response=await api.post('/despatch-notes',{
    invoice_id:invoiceId,
    actual_shipment_at:form.actual_shipment_at,
    driver_name:form.driver_name.trim(),
    driver_national_id:form.driver_national_id.trim(),
    vehicle_plate:form.vehicle_plate.trim(),
    // Boş dorse GÖNDERİLMEZ: sunucu `null` bekliyor, boş dize değil.
    trailer_plate:form.trailer_plate.trim()||null,
    delivery_address:form.delivery_address.trim(),
    delivery_postal_code:form.delivery_postal_code.trim(),
    // MİKTAR DECIMAL DİZESİ, `Number` DEĞİL: dört haneli ölçek JSON'da
    // float'a çevrilirse `0.1+0.2` kalıntısı sunucunun kalan hesabına
    // sızardı (`decimalPayload`, house).
    ...(kismiMi?{lines:secilenSatirlar.map(({item,value})=>({
     invoice_item_id:item.invoice_item_id,
     quantity:decimalPayload(value),
    }))}:{}),
   });
   setNotes(current=>[response.data as DespatchNote,...current]);
   dialogKapat();setForm(BOS_FORM);
   setNotice('e-İrsaliye kaydı oluşturuldu. Göndermek için "Gönder"e basın.');
   load(true);
  }catch(err){
   const detail=apiDetail(err);
   const code=typeof detail?.code==='string'?detail.code:'';
   const kalem=typeof detail?.invoice_item_id==='number'?detail.invoice_item_id:null;
   if(code==='IRSALIYE_TAMAMLANDI'){
    // 409: istek GEÇERLİ, değişen KAYNAĞIN DURUMU. Diyalogda kırmızı bir
    // satır göstermek yanlış olurdu — doğru iş listeyi TAZELEMEK.
    setToast(TAMAMLANDI_MESAJI);dialogKapat();load(true);
   }else if(code==='SEVK_KALEMI_YOK'){
    setDialogHata(SEVK_KALEMI_YOK_MESAJI);
   }else if(code&&SATIR_HATALARI[code]){
    const ek=code==='SEVK_MIKTAR_ASIMI'&&detail?.remaining!==undefined
     ?` Kalan: ${miktar(String(detail.remaining))}.`:'';
    const mesaj=`${SATIR_HATALARI[code]}${ek}`;
    // SATIRI OLMAYAN KOD DİYALOGA DÜŞER. `FATURA_KALEMI_YOK`un kimliği
    // TANIMI GEREĞİ bu faturanın değil; o satır tabloda YOKTUR ve mesaj
    // hiç görünmezdi — kullanıcı sessizce başarısız bir "Oluştur"la kalır.
    // Kural kodun kendisine değil KİMLİĞİN EŞLEŞMESİNE bakıyor: sunucu
    // yarın başka bir koda da tanınmayan kimlik koyarsa aynı yere düşer.
    if(kalem!==null&&items.some(item=>item.invoice_item_id===kalem))
     setSatirHatalari({[kalem]:mesaj});
    else setDialogHata(mesaj);
   }else{
    setDialogHata(errorDetail(err,'e-İrsaliye oluşturulamadı.'));
   }
  }
  finally{setBusy(false)}
 };

 const eylem=async(note:DespatchNote,yol:'submit'|'sync',basarisiz:string)=>{
  setBusy(true);setError('');setNotice('');
  try{
   const response=await api.post(`/despatch-notes/${note.id}/edespatch/${yol}`);
   const govde=response.data as DespatchNote&{
    changed?:boolean;receipt_advice?:{recorded?:number;skipped?:number};
   };
   setNotes(current=>current.map(x=>x.id===note.id?{...x,...govde}:x));
   if(yol==='sync'){
    const ozet=govde.receipt_advice??{};
    setToast(`Durum sorgulandı — ${govde.changed?'değişiklik var':'değişiklik yok'}`
     +` · kaydedilen ${ozet.recorded??0} · atlanan ${ozet.skipped??0}`);
    // YANIT DA TAZELENİR: `sync` bir yanıt belgesi kaydetmiş olabilir ve
    // açık bir bölme eski gövdeyi göstermeye devam ederdi.
    setYanitYenile(current=>current+1);
    // LİSTE DE: `sync` yalnız BU irsaliyeyi döndürür ama kalanları ve
    // `complete` bayrağını değiştirmiş olabilir. `submit`te GEREKMEZ —
    // yanıt gövdesi zaten kaydın kendisi ve kalanlara dokunmuyor.
    load(true);
   }
  }catch(err){setError(errorDetail(err,basarisiz))}
  finally{setBusy(false)}
 };

 if(loading)return <Paper variant="outlined" sx={{p:2}}>
  <Stack direction="row" spacing={1.5} alignItems="center">
   <CircularProgress size={22}/>
   <Typography color="text.secondary">e-İrsaliye durumu yükleniyor…</Typography>
  </Stack>
 </Paper>;

 // `zorunlular` PLAKA + ŞOFÖR dalıdır. Kargo firması dalı sunucuda ve
 // şemada AÇIK ama bu ekranda YOK.
 const zorunlular=Boolean(
  form.actual_shipment_at&&form.driver_name.trim()&&
  form.driver_national_id.trim().length===11&&form.vehicle_plate.trim()&&
  form.delivery_address.trim()&&form.delivery_postal_code.trim().length>=4,
 );
 const kismiDialog=dialogMode==='kismi';
 // SEVK_KALEMI_YOK İSTEMCİDE DE KAPALI: sunucu zaten reddeder ama
 // kullanıcının o 422'yi görmesi gereksiz bir tur demektir.
 const olusturKapali=busy||!zorunlular||(kismiDialog&&!secilenSatirlar.length);

 const olusturButonu=(mode:'tam'|'kismi')=>{
  const buton=<span><Button variant={mode==='tam'?'contained':'outlined'}
    startIcon={mode==='tam'?<LocalShippingIcon/>:<CallSplitIcon/>}
    onClick={()=>dialogAc(mode)} disabled={busy||tamamlandi}
    sx={{minHeight:{xs:44,md:40}}}>
   {mode==='tam'?'e-İrsaliye Oluştur':'Kısmi sevk'}
  </Button></span>;
  // TAMAMLANDIĞINDA İKİSİ DE KAPALI ve SEBEBİ YAZIYOR: kapalı ama sessiz
  // bir buton kullanıcıya "bozuk" diye okunur.
  return tamamlandi?<Tooltip title={TAMAMLANDI_IPUCU} arrow>{buton}</Tooltip>:buton;
 };

 return <Paper variant="outlined" sx={{p:2}}>
  <Stack spacing={1.5}>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between"
     alignItems={{sm:'center'}} gap={1}>
    <Box>
     <Typography variant="h6" fontWeight={800}>e-İrsaliye</Typography>
     <Typography variant="body2" color="text.secondary">
      Sevk irsaliyeleri, kısmi sevk ve sağlayıcı durumu
     </Typography>
    </Box>
    <Stack direction="row" gap={1} flexWrap="wrap">
     {olusturButonu('tam')}
     {olusturButonu('kismi')}
    </Stack>
   </Stack>

   {notice&&<Alert severity="info" onClose={()=>setNotice('')}>{notice}</Alert>}
   {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}

   {!notes.length&&<Typography variant="caption" color="text.secondary">
    Bu fatura için henüz e-İrsaliye oluşturulmadı.
   </Typography>}

   {/* BİR FATURA, ÇOK İRSALİYE (E4b-1): E4a'nın `note?.` varsayımı düştü.
       Her satır KENDİ durumunu, KENDİ eylemlerini ve KENDİ yanıtını
       taşır — tek bir "geçerli irsaliye" kavramı ARTIK YOK. */}
   {notes.map(note=>{
    const status=note.edespatch_status??'NONE';
    const view=STATUS_VIEW[status]??STATUS_VIEW.NONE;
    const gonderilebilir=GONDERILEBILIR.has(status);
    const chip=<Chip label={view.label} color={view.color}
      variant={status==='NONE'?'outlined':'filled'}/>;
    return <Paper key={note.id} variant="outlined" sx={{p:1.5}}
      data-testid={`irsaliye-${note.id}`}>
     <Stack spacing={1}>
      <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between"
        alignItems={{sm:'center'}} gap={1}>
       <Box>
        <Typography variant="body2" fontWeight={700}>
         {note.despatch_number??'-'}
         {note.issue_date?` · ${formatDate(note.issue_date)}`:''}
         {note.lines?` · ${note.lines.length} satır`:''}
        </Typography>
        <Typography variant="caption" color="text.secondary">
         {`ETTN: ${note.despatch_uuid}`}
        </Typography>
       </Box>
       {note.edespatch_last_error
        ?<Tooltip title={note.edespatch_last_error} arrow>{chip}</Tooltip>
        :chip}
      </Stack>

      {status==='UNKNOWN'&&<Alert severity="warning">{BELIRSIZ_UYARISI}</Alert>}
      {status==='FAILED'&&note.edespatch_last_error&&
       <Alert severity="error">{note.edespatch_last_error}</Alert>}

      <Stack direction="row" gap={1} flexWrap="wrap">
       <Button variant="contained" startIcon={<SendIcon/>}
         disabled={!gonderilebilir||busy}
         onClick={()=>eylem(note,'submit','e-İrsaliye gönderilemedi.')}
         sx={{minHeight:{xs:44,md:40}}}>
        {busy?'İşleniyor…':'Gönder'}
       </Button>
       {status!=='NONE'&&<Button variant="outlined" startIcon={<SyncIcon/>}
         disabled={busy}
         onClick={()=>eylem(note,'sync','Durum sorgulanamadı.')}
         sx={{minHeight:{xs:44,md:40}}}>Durumu Sorgula</Button>}
       {status!=='NONE'&&<Button variant="text"
         href={`/api/despatch-notes/${note.id}/edespatch/download?format=xml`}
         sx={{minHeight:{xs:44,md:40}}}>XML</Button>}
      </Stack>

      {/* YANIT BÖLMESİ İZNE BAĞLI: uç `sales` istiyor (`app/auth.py` SEC-3
          önek kuralı) ve depo/rapor kullanıcısı 403 alırdı. Görünüp hata
          veren bir bölme yerine GÖRÜNMEYEN bölme. */}
      {YANIT_GORUNUR.has(status)&&can('sales')&&
       <YanitBolmesi despatchId={note.id} yenile={yanitYenile}/>}
     </Stack>
    </Paper>;
   })}
   {/* PDF BUTONU YOK ve bu bir eksiklik değil: sağlayıcının PDF sözleşmesi
       DOĞRULANMADI (keşif §2.4) ve uç 501 döner. Görünmeyen bir buton,
       her basışta hata veren bir butondan iyidir. */}
  </Stack>

  <Dialog open={dialogMode!==null} onClose={dialogKapat} fullWidth
    maxWidth={kismiDialog?'md':'sm'}>
   <DialogTitle>{kismiDialog?'Kısmi Sevk':'e-İrsaliye Bilgileri'}</DialogTitle>
   <DialogContent>
    <Stack spacing={2} sx={{mt:1}}>
     {dialogHata&&<Alert severity="error">{dialogHata}</Alert>}
     {kismiDialog&&<Box>
      <Typography variant="body2" color="text.secondary" mb={1}>
       Sevk edilecek miktarı yazın; boş ya da sıfır bıraktığınız kalem gönderilmez.
      </Typography>
      <Table size="small">
       <TableHead><TableRow>
        <TableCell>Kalem</TableCell>
        <TableCell align="right">Sevk edilen</TableCell>
        <TableCell align="right">Kalan</TableCell>
        <TableCell align="right">Sevk miktarı</TableCell>
       </TableRow></TableHead>
       <TableBody>
        {/* HİZMET KALEMİ LİSTEDE YOK ve bu SUNUCUDAN geliyor
            (`despatchable-items` LABOR'ı eler) — arayüz ikinci bir
            süzgeç YAZMIYOR ki iki kural iki yerde kaymasın. */}
        {items.map(item=>{
         const kalan=quantityDecimal(item.remaining);
         const hata=satirHatalari[item.invoice_item_id];
         return <TableRow key={item.invoice_item_id}
           data-testid={`sevk-satiri-${item.invoice_item_id}`}>
          <TableCell>{item.description??'-'}</TableCell>
          <TableCell align="right">{miktar(item.despatched)}</TableCell>
          <TableCell align="right">{miktar(item.remaining)}</TableCell>
          <TableCell align="right">
           <TextField size="small" type="number"
             label={item.description??`Kalem ${item.invoice_line_no}`}
             value={quantities[item.invoice_item_id]??''}
             onChange={event=>setQuantities(current=>
              ({...current,[item.invoice_item_id]:event.target.value}))}
             disabled={!kalan.greaterThan(0)}
             error={Boolean(hata)} helperText={hata??' '}
             inputProps={{min:0,max:item.remaining,step:0.0001}}
             sx={{minWidth:160}}/>
          </TableCell>
         </TableRow>;
        })}
       </TableBody>
      </Table>
     </Box>}
     <TextField label="Fiili Sevk Zamanı" type="datetime-local" required
       value={form.actual_shipment_at} onChange={alan('actual_shipment_at')}
       InputLabelProps={{shrink:true}}
       helperText="Malın fiilen yola çıktığı an; fatura tarihinden ayrıdır."/>
     <TextField label="Şoför Adı Soyadı" required
       value={form.driver_name} onChange={alan('driver_name')}/>
     <TextField label="Şoför T.C. Kimlik No" required
       value={form.driver_national_id} onChange={alan('driver_national_id')}
       inputProps={{maxLength:11}}
       helperText="11 hane"/>
     <TextField label="Araç Plakası" required
       value={form.vehicle_plate} onChange={alan('vehicle_plate')}
       inputProps={{maxLength:20}}/>
     <TextField label="Dorse Plakası"
       value={form.trailer_plate} onChange={alan('trailer_plate')}
       inputProps={{maxLength:20}} helperText="İsteğe bağlı"/>
     <TextField label="Teslim Adresi" required multiline minRows={2}
       value={form.delivery_address} onChange={alan('delivery_address')}/>
     {/* POSTA KODU ZORUNLU ve bu bir form kaprisi DEĞİL: GİB şematronu
         DeliveryAddress/PostalZone istiyor ve boş gönderilen belge
         REDDEDİLİYOR (sandbox'ta ölçüldü). Alan burada zorunlu olmasaydı
         kullanıcı hatayı ancak GÖNDERİMDE — geri alınamaz bir denemeden
         sonra — görürdü. */}
     <TextField label="Teslim Posta Kodu" required
       value={form.delivery_postal_code} onChange={alan('delivery_postal_code')}
       inputProps={{maxLength:10}} helperText="GİB zorunlu tutuyor (örn. 34710)"/>
    </Stack>
   </DialogContent>
   <DialogActions>
    <Button onClick={dialogKapat} disabled={busy}>Vazgeç</Button>
    <Button variant="contained" onClick={create} disabled={olusturKapali}>
     {busy?'Kaydediliyor…':'Oluştur'}
    </Button>
   </DialogActions>
  </Dialog>

  <Snackbar open={!!toast} autoHideDuration={6000} onClose={()=>setToast('')}
    anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
   <Alert severity="info" onClose={()=>setToast('')} variant="filled">{toast}</Alert>
  </Snackbar>
 </Paper>;
}

export default DespatchNotePanel;
