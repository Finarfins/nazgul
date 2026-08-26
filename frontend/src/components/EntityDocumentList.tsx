import React,{useCallback,useEffect,useState} from 'react';
import {Accordion,AccordionDetails,AccordionSummary,Alert,Box,Button,Chip,Skeleton,Stack,Typography} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {api,errorDetail,money} from '../api';

/**
 * Carinin TÜM satış/alış geçmişi — yıllara ayrılmış, sayfalı.
 *
 * Kart açılışındaki liste kısa bir önizlemedir. Tek kaynak o olduğu sürece eski
 * geçmiş uygulamada hiçbir yerde görünmüyordu; bu bileşen geçmişi kendi ucundan
 * çeker (`/customers/{id}/documents`).
 *
 * Belgeler YIL BAŞLIKLARI altında gruplanır ve yalnız açılan yılın satırları
 * indirilir. Düz liste 42 belgelik bir caride sayfayı ekranlarca uzatıyordu;
 * ayrıca kullanıcı belirli bir yıla bakmak istediğinde aşağı kaydırmak zorunda
 * kalıyordu. Yıl özeti (adet + tutar) sunucudan TEK istekle gelir, satırlar
 * ancak tıklanınca yüklenir.
 */
const statusLabel:Record<string,string>={draft:'Taslak',pending:'Onay Bekliyor',approved:'Onaylandı',completed:'Tamamlandı',cancelled:'İptal'};
const SAYFA=50;

type Belge={id:number;document_no?:string|null;transaction_date?:string|null;final_total?:number|string|null;status?:string};
type Yil={year:string;count:number;total:number|string};
type YilDurumu={items:Belge[];hasMore:boolean;loading:boolean;error:string};
type Props={
 entityType:'customer'|'supplier';
 entityId:string|number;
 onOpenDocument:(document:Belge)=>void;
};

const bosDurum=():YilDurumu=>({items:[],hasMore:false,loading:false,error:''});

export default function EntityDocumentList({entityType,entityId,onOpenDocument}:Props){
 const [years,setYears]=useState<Yil[]>([]);
 const [total,setTotal]=useState(0);
 const [acik,setAcik]=useState<string|null>(null);
 const [durum,setDurum]=useState<Record<string,YilDurumu>>({});
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 const taban=entityType==='customer'?`/customers/${entityId}/documents`:`/suppliers/${entityId}/documents`;

 // Açılış isteği yalnız yıl özetini almak için; limit=1 ile satır indirmeyiz.
 useEffect(()=>{
  let iptal=false;
  (async()=>{
   try{
    setLoading(true);setError('');
    const {data}=await api.get(taban,{params:{offset:0,limit:1}});
    if(iptal)return;
    const gelen:Yil[]=data.years||[];
    setYears(gelen);
    setTotal(Number(data.total||0));
    setDurum({});
    // En yeni yıl kendiliğinden açılır: kartı açan kişinin aradığı çoğunlukla
    // son hareketlerdir, bir tık daha istemek gereksiz sürtünme olurdu.
    setAcik(gelen.length?gelen[0].year:null);
   }catch(err:any){
    if(!iptal)setError(errorDetail(err,'Belgeler yüklenemedi.'));
   }finally{
    if(!iptal)setLoading(false);
   }
  })();
  return()=>{iptal=true};
 },[taban]);

 const yukle=useCallback(async(yil:string,offset:number)=>{
  setDurum(o=>({...o,[yil]:{...(o[yil]||bosDurum()),loading:true,error:''}}));
  try{
   const {data}=await api.get(taban,{params:{offset,limit:SAYFA,year:yil}});
   setDurum(o=>{
    const onceki=offset===0?[]:(o[yil]?.items||[]);
    return {...o,[yil]:{items:[...onceki,...(data.items||[])],
                        hasMore:Boolean(data.has_more),loading:false,error:''}};
   });
  }catch(err:any){
   const mesaj=errorDetail(err,'Belgeler yüklenemedi.');
   setDurum(o=>({...o,[yil]:{...(o[yil]||bosDurum()),loading:false,error:mesaj}}));
  }
 },[taban]);

 // Açılan yılın satırları henüz indirilmediyse indir.
 useEffect(()=>{
  if(acik&&!durum[acik])void yukle(acik,0);
 },[acik,durum,yukle]);

 if(loading)return <Stack spacing={1}>{[0,1,2].map(i=><Skeleton key={i} variant="rounded" height={58}/>)}</Stack>;
 if(error)return <Alert severity="error">{error}</Alert>;
 if(!years.length)return <Typography color="text.secondary">Kayıt yok.</Typography>;

 return <Stack spacing={1}>
  <Typography sx={{fontSize:14,color:'text.secondary'}}>{`Toplam ${total} belge`}</Typography>
  {years.map(yil=>{
   const d=durum[yil.year]||bosDurum();
   return <Accordion
     key={yil.year}
     expanded={acik===yil.year}
     onChange={(_,acildi)=>setAcik(acildi?yil.year:null)}
     disableGutters
     variant="outlined"
     sx={{overflow:'hidden',borderRadius:2.5,'&:before':{display:'none'}}}
    >
     <AccordionSummary expandIcon={<ExpandMoreIcon/>} sx={{minHeight:60}}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" width="100%" gap={2} pr={1}>
       <Stack direction="row" spacing={1.25} alignItems="center">
        <Typography fontSize={16} fontWeight={900}>{yil.year}</Typography>
        <Chip size="small" label={`${yil.count} belge`}/>
       </Stack>
       <Typography fontSize={16} fontWeight={850}>{money(yil.total)}</Typography>
      </Stack>
     </AccordionSummary>
     <AccordionDetails sx={{p:1.25}}>
      {d.error&&<Alert severity="error" sx={{mb:1}}>{d.error}</Alert>}
      {d.loading&&!d.items.length&&<Stack spacing={1}>{[0,1,2].map(i=><Skeleton key={i} variant="rounded" height={54}/>)}</Stack>}
      <Stack spacing={1}>
       {d.items.map(x=><Stack
         key={x.id}
         role="button"
         tabIndex={0}
         aria-label={`${x.document_no||`Belge #${x.id}`} belgesini aç`}
         onClick={()=>onOpenDocument(x)}
         onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onOpenDocument(x)}}}
         direction={{xs:'column',sm:'row'}}
         justifyContent="space-between"
         alignItems={{sm:'center'}}
         gap={1}
         sx={{p:1.5,minHeight:58,border:'1px solid',borderColor:'divider',borderRadius:2,cursor:'pointer','&:hover':{bgcolor:'action.hover'},'&:focus-visible':{outline:'2px solid',outlineColor:'primary.main'}}}
        >
         <Box>
          <Typography fontSize={15} fontWeight={800}>{x.document_no||`Belge #${x.id}`}</Typography>
          <Typography sx={{fontSize:14,lineHeight:1.55,color:'text.secondary'}}>{x.transaction_date}</Typography>
         </Box>
         <Stack direction="row" spacing={1} alignItems="center">
          <Chip size="small" label={statusLabel[x.status||'']||x.status}/>
          <Typography fontSize={16} fontWeight={900}>{money(x.final_total)}</Typography>
         </Stack>
        </Stack>)}
       {d.hasMore&&<Button onClick={()=>yukle(yil.year,d.items.length)} disabled={d.loading} sx={{alignSelf:'center'}}>
        {d.loading?'Yükleniyor...':`Daha fazla göster (${yil.count-d.items.length} belge daha)`}
       </Button>}
      </Stack>
     </AccordionDetails>
    </Accordion>;
  })}
 </Stack>;
}
