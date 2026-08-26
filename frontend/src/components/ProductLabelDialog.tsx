import React,{useState} from 'react';
import {
 Alert,Button,Dialog,DialogActions,DialogContent,DialogTitle,Divider,FormControlLabel,
 MenuItem,Stack,Switch,TextField,Typography
} from '@mui/material';
import LocalPrintshopIcon from '@mui/icons-material/LocalPrintshop';
import {errorDetail,openAuthenticated,postAuthenticated} from '../api';

type Props={
 open:boolean;
 /** Etiketi basılacak ürünler. Tek kimlik tekil uca, çoklu kimlik toplu uca gider. */
 productIds:number[];
 /** Başlıkta gösterilecek ürün adı (yalnız tek ürün seçiliyken anlamlı). */
 productName?:string;
 onClose:()=>void;
};

// Türkiye'de yaygın termal etiket ölçüleri. Uç, genişlik/yükseklik parametrelerini
// serbest kabul eder; diyalog bunlardan yaygın olanları hazır sunar.
const SIZES=[
 {value:'40x30',width:40,height:30,label:'40 × 30 mm (termal)'},
 {value:'50x30',width:50,height:30,label:'50 × 30 mm (termal)'},
 {value:'60x40',width:60,height:40,label:'60 × 40 mm (termal)'},
];

// Ucun kabul ettiği üst sınırlar (bkz. app/labels.py). Diyalog bunları önden
// denetler; aksi hâlde kullanıcı ham bir 422/400 doğrulama hatası görür.
const MAX_PRODUCTS=500;
const MAX_LABELS=2000;

export default function ProductLabelDialog({open,productIds,productName,onClose}:Props){
 // 'qr' eski 70x40 mm QR etiketidir: çıktısı dondurulmuş ayrı bir uçtur ve
 // seçenek almaz. Yalnız tek ürün için sunulur (toplu uç Code128'dir).
 const [format,setFormat]=useState<'thermal'|'a4'|'qr'>('thermal');
 const [size,setSize]=useState('40x30');
 const [showPrice,setShowPrice]=useState(true);
 const [priceVat,setPriceVat]=useState<'incl'|'excl'>('incl');
 const [copies,setCopies]=useState(1);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');

 const count=productIds.length;
 const selected=SIZES.find(x=>x.value===size)||SIZES[0];
 const total=count*copies;
 // Sessizce kırpmak yerine engelle: hangi ürünlerin basılmadığı belli olmaz.
 const limit=count>MAX_PRODUCTS
  ?`Tek seferde en fazla ${MAX_PRODUCTS} ürün etiketlenebilir (${count} ürün seçili). Listeyi arama veya filtreyle daraltın.`
  :total>MAX_LABELS
   ?`Tek seferde en fazla ${MAX_LABELS} etiket üretilebilir (istenen: ${total}). Kopya sayısını veya ürün sayısını azaltın.`
   :'';

 const close=()=>{if(!busy){setError('');onClose()}};

 const print=async()=>{
  try{
   setBusy(true);setError('');
   if(format==='qr'){
    // Eski etiket: PARAMETRESİZ çağrılır. Sorgu dizesi eklemek sözleşmeyi
    // bozmanın en sessiz yoludur; sahadaki yazıcı/şablonlar buna bağlıdır.
    await openAuthenticated(
     `/products/${productIds[0]}/label.pdf`,
     `urun-${productIds[0]}-etiket.pdf`,
    );
   }else if(count===1){
    // Barkod etiketi AÇIKÇA barcode-label.pdf'ten istenir.
    const query=new URLSearchParams({
     format,
     show_price:String(showPrice),
     price_vat:priceVat,
     copies:String(copies),
     width_mm:String(selected.width),
     height_mm:String(selected.height),
    }).toString();
    await openAuthenticated(
     `/products/${productIds[0]}/barcode-label.pdf?${query}`,
     `urun-${productIds[0]}-barkod-etiket.pdf`,
    );
   }else{
    await postAuthenticated('/products/labels.pdf',{
     product_ids:productIds,
     format,
     show_price:showPrice,
     price_vat:priceVat,
     copies,
     width_mm:selected.width,
     height_mm:selected.height,
    },'urun-etiketleri.pdf');
   }
   onClose();
  }catch(e:any){
   setError(errorDetail(e,'Etiket üretilemedi.'));
  }finally{
   setBusy(false);
  }
 };

 return <Dialog open={open} onClose={close} fullWidth maxWidth="xs">
  <DialogTitle>Etiket Yazdır</DialogTitle>
  <DialogContent>
   <Stack spacing={2} mt={1}>
    {error&&<Alert severity="error">{error}</Alert>}
    {limit&&<Alert severity="warning">{limit}</Alert>}

    <Typography variant="body2" color="text.secondary">
     {count===1?(productName||'Seçili ürün'):`${count} ürün seçildi`}
     {' · '}Barkod: Code128
    </Typography>

    <TextField select label="Biçim" value={format}
     onChange={e=>setFormat(e.target.value as 'thermal'|'a4'|'qr')}>
     <MenuItem value="thermal">Termal barkod etiketi (Code128)</MenuItem>
     <MenuItem value="a4">A4 ızgara — Code128 (sayfada 24 etiket)</MenuItem>
     {count===1&&<MenuItem value="qr">Eski QR etiketi (70 × 40 mm)</MenuItem>}
    </TextField>

    {format==='qr'
     ?<Alert severity="info">
       Eskiden beri kullanılan QR etiketi: 70 × 40 mm, fiyat her zaman basılır.
       Mevcut yazıcı ve şablon ayarlarınızla birebir aynı çıktıyı verir, bu
       yüzden seçenek almaz.
      </Alert>
     :<>
      {format==='thermal'&&<TextField select label="Etiket boyutu" value={size}
       onChange={e=>setSize(e.target.value)}>
       {SIZES.map(x=><MenuItem key={x.value} value={x.value}>{x.label}</MenuItem>)}
      </TextField>}

      <Divider/>

      <FormControlLabel
       control={<Switch checked={showPrice} onChange={e=>setShowPrice(e.target.checked)}/>}
       label="Satış fiyatını yazdır"/>
      {showPrice&&<TextField select label="KDV" value={priceVat}
       onChange={e=>setPriceVat(e.target.value as 'incl'|'excl')}
       helperText="Raf/vitrin etiketinde genellikle KDV dahil kullanılır.">
       <MenuItem value="incl">KDV dahil</MenuItem>
       <MenuItem value="excl">KDV hariç</MenuItem>
      </TextField>}

      <TextField type="number" label="Kopya sayısı" value={copies}
       onChange={e=>setCopies(Math.max(1,Math.min(100,Number(e.target.value)||1)))}
       inputProps={{min:1,max:100}}
       helperText={`Toplam ${total} etiket basılacak.`}/>
     </>}
   </Stack>
  </DialogContent>
  <DialogActions>
   <Button onClick={close} disabled={busy}>Vazgeç</Button>
   <Button variant="contained" startIcon={<LocalPrintshopIcon/>} onClick={print} disabled={busy||!count||!!limit}>
    {busy?'Hazırlanıyor...':'Yazdır / İndir'}
   </Button>
  </DialogActions>
 </Dialog>;
}
