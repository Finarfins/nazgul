import {useEffect,useState} from 'react';
import {
 Alert,Autocomplete,Button,Dialog,DialogActions,DialogContent,DialogTitle,IconButton,MenuItem,
 Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd';

import {api,money} from '../../api';
import type {components} from '../../api/types.gen';

import {
 bordroSatirNo,type BordroSonucu,type CekSenet,type CekSenetGirdisi,DURUM_ETIKETI,type Durum,
 type Eylem,hataMesaji,TUR_ETIKETI,yerelTarih,
} from './cekSenet';

export type Cari={id:number;name:string};
export type Hesap=components['schemas']['PaymentAccountOption'];
type DurumDegistirGovdesi=components['schemas']['DurumDegistir'];

/** Bordro tavanı (backend `BORDRO_TAVANI`). */
const BORDRO_TAVANI=200;

const TARIH_ETIKETI={inputLabel:{shrink:true}} as const;

/** Boş bırakılan isteğe bağlı metin alanı yüke HİÇ girmez (null da değil). */
const secimli=(alanlar:Record<string,string>)=>Object.fromEntries(
 Object.entries(alanlar).map(([anahtar,deger])=>[anahtar,deger.trim()]).filter(([,deger])=>deger),
);

const tutarGecerli=(tutar:string)=>Number(tutar)>0;

// ------------------------------------------------------------ yeni evrak ---

type YeniEvrakProps={
 open:boolean;onClose:()=>void;onSaved:()=>void;
 musteriler:Cari[];tedarikciler:Cari[];
 /** `GET /api/suppliers` `purchases` ister; taşımayan rol verilen evrak GİREMEZ. */
 tedarikciGorunur:boolean;
};

export function YeniEvrakDialog({open,onClose,onSaved,musteriler,tedarikciler,tedarikciGorunur}:YeniEvrakProps){
 const [tur,setTur]=useState<'cek'|'senet'>('cek');
 const [yon,setYon]=useState<'alinan'|'verilen'>('alinan');
 const [cari,setCari]=useState<Cari|null>(null);
 const [tutar,setTutar]=useState('');
 const [vade,setVade]=useState(yerelTarih());
 const [kesideTarihi,setKesideTarihi]=useState('');
 const [seriNo,setSeriNo]=useState('');
 const [bankaAdi,setBankaAdi]=useState('');
 const [subeAdi,setSubeAdi]=useState('');
 const [hesapNo,setHesapNo]=useState('');
 const [kesideci,setKesideci]=useState('');
 const [notlar,setNotlar]=useState('');
 const [hata,setHata]=useState('');
 const [kaydediliyor,setKaydediliyor]=useState(false);

 useEffect(()=>{
  if(!open)return;
  setTur('cek');setYon('alinan');setCari(null);setTutar('');setVade(yerelTarih());setKesideTarihi('');
  setSeriNo('');setBankaAdi('');setSubeAdi('');setHesapNo('');setKesideci('');setNotlar('');setHata('');
 },[open]);

 const kaydet=async()=>{
  if(!cari){setHata(yon==='alinan'?'Müşteri seçin.':'Tedarikçi seçin.');return}
  if(!tutarGecerli(tutar)){setHata('Geçerli bir tutar girin.');return}
  if(!seriNo.trim()){setHata('Seri no zorunludur.');return}
  const govde:CekSenetGirdisi={
   tur,yon,tutar:tutar.trim(),vade,seri_no:seriNo.trim(),
   ...(yon==='alinan'?{customer_id:cari.id}:{supplier_id:cari.id}),
   ...secimli({keside_tarihi:kesideTarihi,banka_adi:bankaAdi,sube_adi:subeAdi,hesap_no:hesapNo,kesideci,notlar}),
  };
  setKaydediliyor(true);setHata('');
  try{
   await api.post('/cek-senetler',govde);
   onSaved();
  }catch(exception){
   setHata(hataMesaji(exception,'Evrak kaydedilemedi'));
  }finally{
   setKaydediliyor(false);
  }
 };

 const secenekler=yon==='alinan'?musteriler:tedarikciler;
 const verilenKapali=yon==='verilen'&&!tedarikciGorunur;
 return <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
  <DialogTitle>Yeni Çek / Senet</DialogTitle>
  <DialogContent><Stack spacing={2} mt={1}>
   {hata&&<Alert severity="error">{hata}</Alert>}
   <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
    <TextField select fullWidth label="Evrak Türü" value={tur} onChange={e=>setTur(e.target.value as 'cek'|'senet')}>
     <MenuItem value="cek">Çek</MenuItem><MenuItem value="senet">Senet</MenuItem>
    </TextField>
    <TextField select fullWidth label="Yön" value={yon} onChange={e=>{setYon(e.target.value as 'alinan'|'verilen');setCari(null)}}>
     <MenuItem value="alinan">Alınan (müşteriden)</MenuItem><MenuItem value="verilen">Verilen (tedarikçiye)</MenuItem>
    </TextField>
   </Stack>
   {verilenKapali
    ?<Alert severity="info">Verilen evrak için tedarikçi listesi gerekir; bu rol tedarikçi listesini göremez.</Alert>
    :<Autocomplete options={secenekler} value={cari} isOptionEqualToValue={(a,b)=>a.id===b.id}
      getOptionLabel={x=>x?.name||''} onChange={(_,v)=>setCari(v)}
      renderInput={p=><TextField {...p} label={yon==='alinan'?'Müşteri':'Tedarikçi'}/>}/>}
   <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
    <TextField fullWidth label="Tutar" type="number" value={tutar} onChange={e=>setTutar(e.target.value)} slotProps={{htmlInput:{min:0,step:'0.01'}}}/>
    <TextField fullWidth label="Seri No" value={seriNo} onChange={e=>setSeriNo(e.target.value)}/>
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
    <TextField fullWidth label="Vade" type="date" value={vade} onChange={e=>setVade(e.target.value)} slotProps={TARIH_ETIKETI}/>
    <TextField fullWidth label="Keşide Tarihi" type="date" value={kesideTarihi} onChange={e=>setKesideTarihi(e.target.value)} slotProps={TARIH_ETIKETI}/>
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
    <TextField fullWidth label="Banka" value={bankaAdi} onChange={e=>setBankaAdi(e.target.value)}/>
    <TextField fullWidth label="Şube" value={subeAdi} onChange={e=>setSubeAdi(e.target.value)}/>
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
    <TextField fullWidth label="Hesap No" value={hesapNo} onChange={e=>setHesapNo(e.target.value)}/>
    <TextField fullWidth label="Keşideci" value={kesideci} onChange={e=>setKesideci(e.target.value)}/>
   </Stack>
   <TextField label="Not" multiline minRows={2} value={notlar} onChange={e=>setNotlar(e.target.value)}/>
  </Stack></DialogContent>
  <DialogActions>
   <Button onClick={onClose}>Vazgeç</Button>
   <Button variant="contained" disabled={kaydediliyor||verilenKapali} onClick={()=>void kaydet()}>Kaydet</Button>
  </DialogActions>
 </Dialog>;
}

// ---------------------------------------------------------------- bordro ---

type BordroSatiri={tur:'cek'|'senet';seriNo:string;tutar:string;vade:string;bankaAdi:string;kesideci:string};
const bosSatir=():BordroSatiri=>({tur:'cek',seriNo:'',tutar:'',vade:yerelTarih(),bankaAdi:'',kesideci:''});

type BordroProps={
 open:boolean;onClose:()=>void;onSaved:(adet:number)=>void;
 musteriler:Cari[];tedarikciler:Cari[];tedarikciGorunur:boolean;
};

/**
 * BORDRO — tek carinin birden çok evrakı tek istekte. Sunucu HEP-YA-HİÇ yazar:
 * bir satır reddedilirse hiçbiri kalmaz ve hata mesajı satır numarasını taşır
 * (`N. satır` / `Satır N:`); o satır tabloda işaretlenir.
 */
export function BordroDialog({open,onClose,onSaved,musteriler,tedarikciler,tedarikciGorunur}:BordroProps){
 const [yon,setYon]=useState<'alinan'|'verilen'>('alinan');
 const [cari,setCari]=useState<Cari|null>(null);
 const [satirlar,setSatirlar]=useState<BordroSatiri[]>([bosSatir()]);
 const [hata,setHata]=useState('');
 const [hataliSatir,setHataliSatir]=useState<number|null>(null);
 const [kaydediliyor,setKaydediliyor]=useState(false);

 useEffect(()=>{
  if(!open)return;
  setYon('alinan');setCari(null);setSatirlar([bosSatir()]);setHata('');setHataliSatir(null);
 },[open]);

 const guncelle=(sira:number,alan:Partial<BordroSatiri>)=>setSatirlar(liste=>liste.map((satir,i)=>i===sira?{...satir,...alan}:satir));
 const satirEkle=()=>setSatirlar(liste=>liste.length>=BORDRO_TAVANI?liste:[...liste,{...bosSatir(),vade:liste[liste.length-1]?.vade||yerelTarih()}]);
 const satirSil=(sira:number)=>setSatirlar(liste=>liste.length<=1?liste:liste.filter((_,i)=>i!==sira));

 const kaydet=async()=>{
  setHataliSatir(null);
  if(!cari){setHata(yon==='alinan'?'Müşteri seçin.':'Tedarikçi seçin.');return}
  const eksik=satirlar.findIndex(s=>!s.seriNo.trim()||!tutarGecerli(s.tutar)||!s.vade);
  if(eksik>=0){setHata(`${eksik+1}. satır: seri no, tutar ve vade zorunludur.`);setHataliSatir(eksik+1);return}
  const govde:{satirlar:CekSenetGirdisi[]}={satirlar:satirlar.map(s=>({
   tur:s.tur,yon,tutar:s.tutar.trim(),vade:s.vade,seri_no:s.seriNo.trim(),
   ...(yon==='alinan'?{customer_id:cari.id}:{supplier_id:cari.id}),
   ...secimli({banka_adi:s.bankaAdi,kesideci:s.kesideci}),
  }))};
  setKaydediliyor(true);setHata('');
  try{
   const yanit=await api.post<BordroSonucu>('/cek-senetler/bordro',govde);
   onSaved(yanit.data.ids.length);
  }catch(exception){
   const mesaj=hataMesaji(exception,'Bordro kaydedilemedi');
   setHata(`Bordro kaydedilmedi (hiçbir satır yazılmadı). ${mesaj}`);
   setHataliSatir(bordroSatirNo(mesaj));
  }finally{
   setKaydediliyor(false);
  }
 };

 const toplam=satirlar.reduce((t,s)=>t+Math.round(Number(s.tutar||0)*100),0)/100;
 const verilenKapali=yon==='verilen'&&!tedarikciGorunur;
 return <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
  <DialogTitle>Bordro Girişi</DialogTitle>
  <DialogContent><Stack spacing={2} mt={1}>
   {hata&&<Alert severity="error">{hata}</Alert>}
   <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
    <TextField select label="Yön" value={yon} sx={{minWidth:220}} onChange={e=>{setYon(e.target.value as 'alinan'|'verilen');setCari(null)}}>
     <MenuItem value="alinan">Alınan (müşteriden)</MenuItem><MenuItem value="verilen">Verilen (tedarikçiye)</MenuItem>
    </TextField>
    {verilenKapali
     ?<Alert severity="info" sx={{flex:1}}>Verilen evrak için tedarikçi listesi gerekir; bu rol tedarikçi listesini göremez.</Alert>
     :<Autocomplete sx={{flex:1}} options={yon==='alinan'?musteriler:tedarikciler} value={cari}
       isOptionEqualToValue={(a,b)=>a.id===b.id} getOptionLabel={x=>x?.name||''} onChange={(_,v)=>setCari(v)}
       renderInput={p=><TextField {...p} label={yon==='alinan'?'Müşteri':'Tedarikçi'}/>}/>}
   </Stack>
   <TableContainer sx={{maxHeight:420}}>
    <Table size="small" stickyHeader>
     <TableHead><TableRow>
      <TableCell>#</TableCell><TableCell>Tür</TableCell><TableCell>Seri No</TableCell><TableCell>Tutar</TableCell>
      <TableCell>Vade</TableCell><TableCell>Banka</TableCell><TableCell>Keşideci</TableCell><TableCell/>
     </TableRow></TableHead>
     <TableBody>
      {satirlar.map((satir,sira)=><TableRow key={sira} data-testid={`bordro-satir-${sira+1}`}
        selected={hataliSatir===sira+1} sx={hataliSatir===sira+1?{'& td':{bgcolor:'rgba(211,47,47,.10)'}}:undefined}>
       <TableCell>{sira+1}</TableCell>
       <TableCell><TextField select size="small" value={satir.tur} onChange={e=>guncelle(sira,{tur:e.target.value as 'cek'|'senet'})}
         slotProps={{htmlInput:{'aria-label':`${sira+1}. satır tür`}}}>
        <MenuItem value="cek">Çek</MenuItem><MenuItem value="senet">Senet</MenuItem>
       </TextField></TableCell>
       <TableCell><TextField size="small" value={satir.seriNo} onChange={e=>guncelle(sira,{seriNo:e.target.value})}
         slotProps={{htmlInput:{'aria-label':`${sira+1}. satır seri no`}}}/></TableCell>
       <TableCell><TextField size="small" type="number" value={satir.tutar} onChange={e=>guncelle(sira,{tutar:e.target.value})}
         slotProps={{htmlInput:{'aria-label':`${sira+1}. satır tutar`,min:0,step:'0.01'}}}/></TableCell>
       <TableCell><TextField size="small" type="date" value={satir.vade} onChange={e=>guncelle(sira,{vade:e.target.value})}
         slotProps={{htmlInput:{'aria-label':`${sira+1}. satır vade`}}}/></TableCell>
       <TableCell><TextField size="small" value={satir.bankaAdi} onChange={e=>guncelle(sira,{bankaAdi:e.target.value})}
         slotProps={{htmlInput:{'aria-label':`${sira+1}. satır banka`}}}/></TableCell>
       <TableCell><TextField size="small" value={satir.kesideci} onChange={e=>guncelle(sira,{kesideci:e.target.value})}
         slotProps={{htmlInput:{'aria-label':`${sira+1}. satır keşideci`}}}/></TableCell>
       <TableCell><IconButton size="small" color="error" disabled={satirlar.length<=1} aria-label={`${sira+1}. satırı sil`}
         onClick={()=>satirSil(sira)}><DeleteIcon fontSize="small"/></IconButton></TableCell>
      </TableRow>)}
     </TableBody>
    </Table>
   </TableContainer>
   <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1}>
    <Button startIcon={<PlaylistAddIcon/>} disabled={satirlar.length>=BORDRO_TAVANI} onClick={satirEkle}>Satır Ekle</Button>
    <Typography variant="body2" color="text.secondary">{satirlar.length} / {BORDRO_TAVANI} satır · Toplam {money(toplam)}</Typography>
   </Stack>
  </Stack></DialogContent>
  <DialogActions>
   <Button onClick={onClose}>Vazgeç</Button>
   <Button variant="contained" disabled={kaydediliyor||verilenKapali} onClick={()=>void kaydet()}>Bordroyu Kaydet</Button>
  </DialogActions>
 </Dialog>;
}

// ---------------------------------------------------------- durum değiştir ---

type DurumProps={
 eylem:Eylem|null;evrak:CekSenet|null;onClose:()=>void;onDone:()=>void;
 hesaplar:Hesap[];tedarikciler:Cari[];
};

/**
 * Tek bir `POST /api/cek-senetler/{id}/durum-degistir`. Yük hedefe göre
 * kurulur ve backend `_IZINLI_ALANLAR` ile birebirdir: ilgisiz alan GÖNDERİLMEZ
 * (sunucu onu 422 ile reddeder). Sunucunun 409/422 mesajı pencerede gösterilir.
 */
export function DurumDialog({eylem,evrak,onClose,onDone,hesaplar,tedarikciler}:DurumProps){
 const [notMetni,setNotMetni]=useState('');
 const [hesapId,setHesapId]=useState('');
 const [tarih,setTarih]=useState(yerelTarih());
 const [tedarikci,setTedarikci]=useState<Cari|null>(null);
 const [hata,setHata]=useState('');
 const [kaydediliyor,setKaydediliyor]=useState(false);
 const acik=Boolean(eylem&&evrak);

 useEffect(()=>{
  if(!acik)return;
  setNotMetni('');setHesapId('');setTarih(yerelTarih());setTedarikci(null);setHata('');
 },[acik,eylem?.anahtar,evrak?.id]);

 if(!eylem||!evrak)return null;
 // Tahsil hesabı yalnız kasa/banka olabilir (backend `TAHSIL_HESAP_TIPLERI`; POS değil).
 const tahsilHesaplari=hesaplar.filter(h=>h.is_active&&(h.account_type==='cash'||h.account_type==='bank'));

 const kaydet=async()=>{
  const govde:DurumDegistirGovdesi={hedef:eylem.hedef};
  if(eylem.hedef==='tahsil_edildi'){
   if(!hesapId||!tarih){setHata('Tahsil hesabı ve tahsil tarihi zorunludur.');return}
   govde.tahsil_hesap_id=Number(hesapId);govde.tahsil_tarihi=tarih;
  }
  if(eylem.hedef==='ciro_edildi'){
   if(!tedarikci){setHata('Ciro edilecek tedarikçiyi seçin.');return}
   govde.endorsed_supplier_id=tedarikci.id;
   if(tarih)govde.endorsed_date=tarih;
  }
  if(notMetni.trim())govde.not_metni=notMetni.trim();
  setKaydediliyor(true);setHata('');
  try{
   await api.post(`/cek-senetler/${evrak.id}/durum-degistir`,govde);
   onDone();
  }catch(exception){
   setHata(hataMesaji(exception,'Durum değiştirilemedi'));
  }finally{
   setKaydediliyor(false);
  }
 };

 return <Dialog open={acik} onClose={onClose} maxWidth="sm" fullWidth>
  <DialogTitle>{eylem.baslik}</DialogTitle>
  <DialogContent><Stack spacing={2} mt={1}>
   <Alert severity="info" icon={false}>
    {TUR_ETIKETI[evrak.tur]||evrak.tur} {evrak.seri_no} · {money(evrak.tutar)} · Vade {evrak.vade}
    <br/>{DURUM_ETIKETI[evrak.portfoy_durumu as Durum]||evrak.portfoy_durumu} → {DURUM_ETIKETI[eylem.hedef]}
   </Alert>
   {hata&&<Alert severity="error">{hata}</Alert>}
   {eylem.hedef==='tahsil_edildi'&&<>
    <TextField select label="Tahsil Hesabı (Kasa / Banka)" value={hesapId} onChange={e=>setHesapId(e.target.value)}
      helperText={tahsilHesaplari.length?undefined:'Aktif kasa/banka hesabı yok.'}>
     {tahsilHesaplari.map(h=><MenuItem key={h.id} value={String(h.id)}>{h.name} ({h.account_type==='cash'?'Kasa':'Banka'})</MenuItem>)}
    </TextField>
    <TextField label="Tahsil Tarihi" type="date" value={tarih} onChange={e=>setTarih(e.target.value)} slotProps={TARIH_ETIKETI}/>
   </>}
   {eylem.hedef==='ciro_edildi'&&<>
    <Autocomplete options={tedarikciler} value={tedarikci} isOptionEqualToValue={(a,b)=>a.id===b.id}
      getOptionLabel={x=>x?.name||''} onChange={(_,v)=>setTedarikci(v)}
      renderInput={p=><TextField {...p} label="Ciro Edilen Tedarikçi"/>}/>
    <TextField label="Ciro Tarihi" type="date" value={tarih} onChange={e=>setTarih(e.target.value)} slotProps={TARIH_ETIKETI}/>
   </>}
   <TextField label="Not" multiline minRows={2} value={notMetni} onChange={e=>setNotMetni(e.target.value)}/>
  </Stack></DialogContent>
  <DialogActions>
   <Button onClick={onClose}>Vazgeç</Button>
   <Button variant="contained" disabled={kaydediliyor} onClick={()=>void kaydet()}>{eylem.etiket}</Button>
  </DialogActions>
 </Dialog>;
}
