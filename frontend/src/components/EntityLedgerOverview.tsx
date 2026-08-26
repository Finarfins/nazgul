import React, {useState} from 'react';
import {
 Accordion,AccordionDetails,AccordionSummary,Alert,Box,Button,Card,CardContent,
  Chip,CircularProgress,Grid,Stack,Typography
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import EditIcon from '@mui/icons-material/Edit';
import PrintIcon from '@mui/icons-material/Print';
import Inventory2OutlinedIcon from '@mui/icons-material/Inventory2Outlined';
import {api,errorDetail,money,openAuthenticated} from '../api';

type Props={
 type:'customer'|'supplier';
  documents:any[];
  payments:any[];
  chargeDocuments?:any[];
  onOpenDocument:(id:number)=>void;
  onOpenProduct?:(id:number)=>void;
  /** Carinin TOPLAM belge sayısı (yalnız önizlemedeki değil). */
  documentTotal?:number;
  /** "Tümünü gör" tıklaması: kart bunu Satış/Alış sekmesine geçmek için kullanır. */
  onShowAllDocuments?:()=>void;
};

const paymentLabel:Record<string,string>={
 cash:'Nakit',card:'Kart / POS',bank_transfer:'Havale / EFT',credit:'Vadeli',
 check:'Çek',promissory_note:'Senet',
};

const metadataSx={fontSize:13.2,color:'text.secondary',lineHeight:1.55};

export default function EntityLedgerOverview({type,documents,payments,chargeDocuments=[],onOpenDocument,onOpenProduct,documentTotal=0,onShowAllDocuments}:Props){
 const [expanded,setExpanded]=useState<number|null>(null);
 const [details,setDetails]=useState<Record<number,any>>({});
 const [loadingId,setLoadingId]=useState<number|null>(null);
 const [error,setError]=useState('');

 const toggle=async(document:any,isExpanded:boolean)=>{
  if(!isExpanded){setExpanded(null);return}
  setExpanded(document.id);
  if(details[document.id])return;
  setLoadingId(document.id);setError('');
  try{
   const response=await api.get(`/${type==='customer'?'orders':'purchases'}/${document.id}`);
   setDetails(current=>({...current,[document.id]:response.data}));
  }catch(err){
   setError(errorDetail(err,'Belge kalemleri yüklenemedi.'));
  }finally{setLoadingId(null)}
 };

 return <Grid container spacing={2}>
  <Grid size={{xs:12,lg:7}}>
   <Card variant="outlined" sx={{height:'100%'}}><CardContent>
    <Typography variant="h6" mb={1}>{type==='customer'?'Satışlar':'Alışlar'}</Typography>
    {error&&<Alert severity="error" sx={{mb:1}}>{error}</Alert>}
    {!documents.length&&<Typography color="text.secondary">Henüz belge yok.</Typography>}
     <Stack spacing={1}>
      {documents.slice(0,8).map(document=><Accordion
      key={document.id}
      expanded={expanded===document.id}
      onChange={(_,value)=>void toggle(document,value)}
      disableGutters
      variant="outlined"
      sx={{
       overflow:'hidden',
       borderRadius:2.5,
       '&:before':{display:'none'},
       '&.Mui-expanded':{boxShadow:'0 10px 28px rgba(28,38,32,.08)'},
      }}
     >
      <AccordionSummary
       expandIcon={<ExpandMoreIcon/>}
       sx={{
        minHeight:62,
        bgcolor:expanded===document.id?'action.hover':'transparent',
        '& .MuiAccordionSummary-content':{my:1.25},
       }}
      >
       <Stack direction="row" justifyContent="space-between" alignItems="center" width="100%" gap={2} pr={1}>
        <Box>
         <Typography fontSize={15} fontWeight={800}>{document.document_no||`Belge #${document.id}`}</Typography>
         <Typography variant="body2" color="text.secondary">{document.transaction_date}</Typography>
        </Box>
        <Typography fontSize={16} fontWeight={850}>{money(document.final_total)}</Typography>
       </Stack>
      </AccordionSummary>
      <AccordionDetails sx={{p:0}}>
       {loadingId===document.id?<Box textAlign="center" py={2}><CircularProgress size={24}/></Box>:<>
        <Stack>
         {(details[document.id]?.items||[]).map((item:any)=><Stack
          key={item.id}
          component={onOpenProduct&&item.product_id?'button':'div'}
          type={onOpenProduct&&item.product_id?'button':undefined}
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          gap={2}
          onClick={onOpenProduct&&item.product_id?()=>onOpenProduct(Number(item.product_id)):undefined}
          sx={{
           width:'100%',
           minHeight:68,
           px:{xs:1.5,sm:2},
           py:1.25,
           border:0,
           bgcolor:'transparent',
           color:'text.primary',
           font:'inherit',
           textAlign:'left',
           cursor:onOpenProduct&&item.product_id?'pointer':'default',
           transition:'background-color .15s ease',
           '&:not(:last-child)':{borderBottom:'1px solid',borderColor:'divider'},
           '&:hover':{bgcolor:'action.hover'},
          }}
         >
          <Stack direction="row" alignItems="center" gap={1.25} minWidth={0}>
           <Box sx={{width:36,height:36,borderRadius:2,display:'grid',placeItems:'center',flex:'0 0 auto',bgcolor:'brandTint',color:'primary.main'}}>
            <Inventory2OutlinedIcon sx={{fontSize:19}}/>
           </Box>
           <Box minWidth={0}>
            <Typography fontSize={16.5} lineHeight={1.35} fontWeight={800} noWrap>{item.product_name}</Typography>
            <Typography fontSize={13.5} color="text.secondary">{item.quantity} adet × {money(item.unit_price)}</Typography>
           </Box>
          </Stack>
          <Typography fontSize={16} fontWeight={850} whiteSpace="nowrap">{money(item.line_total)}</Typography>
         </Stack>)}
         {details[document.id]&&!(details[document.id]?.items||[]).length&&<Typography color="text.secondary" px={2} py={2}>Kalem bulunamadı.</Typography>}
        </Stack>
        <Stack direction="row" spacing={1} px={1.5} py={1.25} bgcolor="action.hover" borderTop="1px solid" borderColor="divider">
         <Button size="small" variant="outlined" startIcon={<EditIcon/>} onClick={()=>onOpenDocument(document.id)}>Belgeyi Aç</Button>
         <Button size="small" variant="text" startIcon={<PrintIcon/>} onClick={()=>openAuthenticated(`/documents/${type==='customer'?'orders':'purchases'}/${document.id}/pdf`)}>Yazdır</Button>
        </Stack>
       </>}
      </AccordionDetails>
     </Accordion>)}
      {/* Bu liste bir ÖNİZLEMEDİR. Toplam belge sayısı söylenmezse kısalma
          sessiz veri kaybı gibi görünüyor: 30 satışı olan bir caride yalnız en
          yeni 8'i çıkıyor ve kullanıcı geçmişinin silindiğini sanıyordu. */}
      {documentTotal>documents.slice(0,8).length&&<Button
        size="small"
        onClick={onShowAllDocuments}
        sx={{alignSelf:'flex-start'}}
       >
        {`${documentTotal} belgenin ${documents.slice(0,8).length} tanesi · tümünü gör`}
       </Button>}
       </Stack>
      </CardContent></Card>
    </Grid>
   <Grid size={{xs:12,lg:5}}>
    <Card variant="outlined" sx={{height:'100%'}}><CardContent>
     <Typography variant="h6" mb={1}>{type==='customer'?'Tahsilatlar':'Ödemeler'}</Typography>
     {!payments.length&&<Typography color="text.secondary">Henüz ödeme hareketi yok.</Typography>}
     <Stack spacing={1}>
      {payments.slice(0,8).map(payment=><Stack key={payment.id} direction="row" justifyContent="space-between" gap={2} sx={{py:1,borderBottom:'1px solid',borderColor:'divider'}}>
       <Box><Typography fontWeight={700}>{payment.payment_date}</Typography><Typography variant="caption" color="text.secondary">{paymentLabel[payment.payment_method]||payment.payment_method}{payment.note?` · ${payment.note}`:''}</Typography></Box>
       <Typography fontWeight={800}>{money(payment.amount)}</Typography>
      </Stack>)}
     </Stack>
     {chargeDocuments.length>0&&<>
      <Typography variant="subtitle2" fontWeight={900} sx={{mt:2}}>Servis / Gecikme Bedelleri</Typography>
      <Stack spacing={1} sx={{mt:1}}>
       {chargeDocuments.map((charge:any)=><Stack
        key={charge.id}
        direction={{xs:'column',sm:'row'}}
        justifyContent="space-between"
        alignItems={{sm:'center'}}
        gap={1}
        sx={{p:1.5,minHeight:58,border:'1px solid',borderColor:'divider',borderRadius:2,bgcolor:'action.hover'}}
       >
        <Box>
         <Stack direction="row" spacing={1} alignItems="center">
          <Chip size="small" color={charge.charge_type==='service_fee'?'info':'warning'} label={charge.charge_type==='service_fee'?'Servis':'Gecikme'}/>
          <Typography fontSize={15} fontWeight={800}>{charge.document_no}</Typography>
         </Stack>
         <Typography sx={metadataSx}>Vade {charge.due_date}</Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
         {Number(charge.applied)>0&&<Typography sx={metadataSx}>Tahsil {money(charge.applied)}</Typography>}
         <Typography fontSize={16} fontWeight={900}>{money(charge.remaining)}</Typography>
        </Stack>
       </Stack>)}
      </Stack>
     </>}
    </CardContent></Card>
   </Grid>
  </Grid>;
}
