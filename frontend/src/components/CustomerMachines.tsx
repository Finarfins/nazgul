import React,{useEffect,useState} from 'react';
import {Alert,Box,Card,CardContent,Chip,Skeleton,Stack,Tab,Typography} from '@mui/material';
import {useNavigate} from 'react-router-dom';
import {api,errorDetail} from '../api';
import {MACHINE_STATUS_LABELS} from './MachineDialog';

// Cari kartında makine parkı sekmesi. Yalnız müşteride anlamlı: makineler
// müşteriye ait (machines.customer_id), tedarikçiye değil.
//
// ...rest ŞART: MUI <Tabs> çocuklarına onChange/value/selected/indicator
// proplarını enjekte eder. Sarmalayıcı bunları Tab'e geçirmezse sekme görünür
// ama TIKLANMAZ — sessiz bir kusur, çünkü ekranda her şey yerinde durur.
export function CustomerMachinesTab({allowed,...rest}:{allowed:boolean}&Record<string,unknown>){
 return allowed?<Tab label="Makineler" {...rest}/>:null;
}

const statusColor:Record<string,'default'|'info'|'warning'|'success'|'error'>={
 active:'success',maintenance:'warning',idle:'info',sold:'default',scrapped:'error',
};

type Machine={
 id:number;brand?:string|null;model?:string|null;serial_number?:string|null;
 chassis_number?:string|null;status?:string|null;working_hours?:string|number|null;
 model_year?:number|null;is_active?:boolean;
};

export default function CustomerMachines({customerId}:{customerId:number}){
 const nav=useNavigate();
 const [items,setItems]=useState<Machine[]|null>(null);
 const [error,setError]=useState('');

 useEffect(()=>{
  let cancelled=false;
  setItems(null);setError('');
  // Mevcut, kiracı kapsamlı uç. Sekme açılınca çekilir: hiç açmayan kullanıcı
  // için maliyeti sıfır, cari kartın kendi yükü de büyümez.
  api.get<Machine[]>('/machines',{params:{customer_id:customerId,active:'all',limit:100}})
   .then(r=>{if(!cancelled)setItems(r.data)})
   .catch(e=>{if(!cancelled){setError(errorDetail(e,'Makineler yüklenemedi.'));setItems([])}});
  return ()=>{cancelled=true};
 },[customerId]);

 if(error)return <Alert severity="error">{error}</Alert>;
 if(items===null)return <Stack spacing={1}>
  {[0,1,2].map(i=><Skeleton key={i} variant="rounded" height={84}/>)}
 </Stack>;
 if(!items.length)return <Typography color="text.secondary">Bu müşteriye tanımlı makine yok.</Typography>;

 return <Stack spacing={1}>
  {items.map(machine=>{
   const title=[machine.brand,machine.model].filter(Boolean).join(' ')||`Makine #${machine.id}`;
   const open=()=>nav(`/makineler/${machine.id}`);
   return <Card
    key={machine.id}
    role="button"
    tabIndex={0}
    aria-label={`${title} makine kartını aç`}
    onClick={open}
    onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();open()}}}
    variant="outlined"
   >
    <CardContent sx={{p:1.5,'&:last-child':{pb:1.5}}}>
     <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1} alignItems={{sm:'center'}}>
      <Box>
       <Stack direction="row" spacing={1} alignItems="center">
        <Typography fontSize={15} fontWeight={800}>{title}</Typography>
        {machine.status&&<Chip size="small" color={statusColor[machine.status]||'default'}
         label={MACHINE_STATUS_LABELS[machine.status]||machine.status}/>}
        {machine.is_active===false&&<Chip size="small" variant="outlined" label="Pasif"/>}
       </Stack>
       <Typography sx={{fontSize:14,color:'text.secondary',lineHeight:1.55}}>
        Seri {machine.serial_number||'-'}
        {machine.chassis_number?` · Şasi ${machine.chassis_number}`:''}
        {machine.model_year?` · ${machine.model_year}`:''}
       </Typography>
      </Box>
      {machine.working_hours!==null&&machine.working_hours!==undefined&&
       <Typography fontSize={15} fontWeight={800} sx={{whiteSpace:'nowrap'}}>
        {Number(machine.working_hours).toLocaleString('tr-TR')} saat
       </Typography>}
     </Stack>
    </CardContent>
   </Card>;
  })}
 </Stack>;
}
