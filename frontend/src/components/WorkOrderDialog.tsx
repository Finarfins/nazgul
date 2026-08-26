import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Autocomplete,Button,Dialog,DialogActions,DialogContent,DialogTitle,Divider,IconButton,MenuItem,Stack,TextField,Typography} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import {api,money} from '../api';
import {decimal,decimalPayload,moneyDecimal,partLineTotalDecimal} from '../utils/documentMoney';
import {isLineBlank,useAutoTrailingLine} from '../utils/useAutoTrailingLine';
import {WO_PRIORITY_LABELS,WARRANTY_TYPE_LABELS} from '../workOrderConstants';

// PUT /api/work-orders/:id tam değiştirme (full replacement) yaptığı için dialog
// WorkOrderCreate'in TÜM alanlarını taşımalı; aksi halde düzenleme kayıtlı
// alanları sıfırlar. customer_id makine sahibinden türetilir: makinenin sahibi
// varsa backend onu zorlar; yoksa kullanıcı müşteri seçer.
// GET /api/machines sözleşmesi limit'i 1..500 ile sınırlar
// (backend/app/routers/machines.py list_machines, Query(le=500)); üstü 422 döner.
export const MACHINE_OPTIONS_LIMIT=500;
export const MACHINE_SEARCH_DEBOUNCE_MS=250;
export const PART_SEARCH_DEBOUNCE_MS=250;
const MACHINE_OPTIONS_ERROR='Makineler yüklenemedi — tekrar deneyin';

const blank={
 machine_id:null as number|null,customer_id:null as number|null,technician_id:null as number|null,
 opened_at:null as string|null,
 status:'OPEN',scheduled_date:'',
 work_order_no:'',priority:'NORMAL',complaint:'',diagnosis:'',repair_summary:'',technician_notes:'',
 estimated_hours:'0',actual_hours:'0',labor_rate:'0',warranty_type:'NONE',warranty_percent:'' as number|string,
};
type PartLine={product_id:number|null;quantity:number;unit_price:number;discount:number;tax_rate:number};
const blankPartLine=():PartLine=>({product_id:null,quantity:1,unit_price:0,discount:0,tax_rate:0});

export default function WorkOrderDialog({open,id,machineId,onClose,onSaved}:{open:boolean;id?:number|null;machineId?:number|null;onClose:()=>void;onSaved:(workOrder?:any)=>void}){
 const [v,setV]=useState<any>(blank);
 const [machines,setMachines]=useState<any[]>([]);
 const [machineQuery,setMachineQuery]=useState('');
 const [machineLoading,setMachineLoading]=useState(false);
 const [machineOptionsError,setMachineOptionsError]=useState(false);
 const [customers,setCustomers]=useState<any[]>([]);
 const [technicians,setTechnicians]=useState<any[]>([]);
 const [warehouses,setWarehouses]=useState<any[]>([]);
 const [partWarehouseId,setPartWarehouseId]=useState<number|null>(null);
 const [partProducts,setPartProducts]=useState<any[]>([]);
 const [partProductsTruncated,setPartProductsTruncated]=useState(false);
 const [partQuery,setPartQuery]=useState('');
 const [partLines,setPartLines]=useState<PartLine[]>([blankPartLine()]);
 const [error,setError]=useState('');
 const [saving,setSaving]=useState(false);
 useEffect(()=>{if(!open)return;setError('');setMachineQuery('');setMachineOptionsError(false);
  // Makine listesi zorunlu alanı besler; yükleme hatası yutulursa kutu hata
  // yerine "No options" gösterir — canlıda limit=1000 → 422 böyle gizlenmişti.
  Promise.all([
   api.get('/machines',{params:{active:'all',limit:MACHINE_OPTIONS_LIMIT}}).then(r=>r.data||[])
    .catch(()=>{setMachineOptionsError(true);setError('Makine listesi yüklenemedi.');return[]}),
   api.get('/customers',{params:{active:'all'}}).then(r=>r.data||[]).catch(()=>[]),
   api.get('/work-orders/technicians').then(r=>r.data||[]).catch(()=>[]),
   api.get('/warehouses').then(r=>r.data||[]).catch(()=>[]),
  ]).then(([m,c,t,w])=>{setMachines(m);setCustomers(c);setTechnicians(t);setWarehouses(w);if(!id&&w[0])setPartWarehouseId(w[0].id)});
  if(id)api.get(`/work-orders/${id}`).then(r=>{const d=r.data;setV({...blank,
    machine_id:d.machine_id,customer_id:d.customer_id,technician_id:d.technician_id,
    opened_at:d.opened_at??null,
    status:d.status||'OPEN',scheduled_date:d.scheduled_date?String(d.scheduled_date).slice(0,16):'',
    work_order_no:d.work_order_no||'',priority:d.priority||'NORMAL',complaint:d.complaint||'',
    diagnosis:d.diagnosis||'',repair_summary:d.repair_summary||'',technician_notes:d.technician_notes||'',
    estimated_hours:String(d.estimated_hours??'0'),actual_hours:String(d.actual_hours??'0'),labor_rate:String(d.labor_rate??'0'),
    warranty_type:d.warranty_type||'NONE',warranty_percent:d.warranty_percent??'',
   })}).catch(e=>setError(e.response?.data?.detail||e.message));
  else{setV({...blank,machine_id:machineId||null});setPartLines([blankPartLine()]);setPartProductsTruncated(false);setPartQuery('')}
 },[open,id,machineId]);
 useEffect(()=>{
  const q=machineQuery.trim();
  if(!open||!q)return;
  const controller=new AbortController();
  const timer=window.setTimeout(async()=>{
   setMachineLoading(true);setMachineOptionsError(false);
   try{
    const response=await api.get('/machines',{
     params:{active:'all',limit:MACHINE_OPTIONS_LIMIT,q},
     signal:controller.signal,
    });
    setMachines(response.data||[]);
   }catch(e:any){
    if(e?.code!=='ERR_CANCELED'){
     setMachines([]);
     setMachineOptionsError(true);
    }
   }finally{
    if(!controller.signal.aborted)setMachineLoading(false);
   }
  },MACHINE_SEARCH_DEBOUNCE_MS);
  return()=>{window.clearTimeout(timer);controller.abort()};
 },[open,machineQuery]);
 useEffect(()=>{
  if(!open||id||!partWarehouseId){setPartProducts([]);setPartProductsTruncated(false);return}
  const q=partQuery.trim();
  const timer=window.setTimeout(()=>{
   const params:any={limit:2000,warehouse_id:partWarehouseId,include_meta:true};
   if(q)params.q=q;
   api.get('/products',{params}).then(r=>{setPartProducts(r.data?.items||[]);setPartProductsTruncated(Boolean(r.data?.has_more))}).catch(()=>{setPartProducts([]);setPartProductsTruncated(false)})
  },PART_SEARCH_DEBOUNCE_MS);
  return()=>window.clearTimeout(timer);
 },[open,id,partWarehouseId,partQuery]);
 useAutoTrailingLine(open&&!id,partLines,setPartLines,blankPartLine);
 const selectedMachine=useMemo(()=>machines.find(m=>m.id===v.machine_id)||null,[machines,v.machine_id]);
 // Seçilen makinenin sahibi varsa müşteri otomatik gelir ve kilitlenir; yoksa
 // kullanıcı müşteri seçmelidir.
 const machineOwnerId=selectedMachine?.customer_id??null;
 const effectiveCustomerId=machineOwnerId??v.customer_id;
 const selectedCustomer=customers.find(c=>c.id===effectiveCustomerId)||null;
 const f=(k:string)=>(e:any)=>setV({...v,[k]:e.target.value});
 const laborTotal=useMemo(()=>{const h=Number(v.actual_hours||0);const rate=Number(v.labor_rate||0);return Number.isFinite(h*rate)?h*rate:0},[v.actual_hours,v.labor_rate]);
 // Parça ara toplamı: seçili satırların Decimal toplamı (float birikimi yok).
 const partsSubtotal=useMemo(()=>partLines.filter(line=>line.product_id).reduce((sum,line)=>sum.plus(partLineTotalDecimal(line)),decimal(0)),[partLines]);
 // Birim fiyatı 0/negatif olan seçili parça varsa kaydetme engellenir (gelir
 // kaybı riski). Uyarı yeterli değildi; artık kayıt bloklanır.
 const hasZeroPricedPart=useMemo(()=>partLines.some(line=>line.product_id&&!(Number(line.unit_price)>0)),[partLines]);
 // İşçilik de Decimal ile hesaplanıp parçalarla toplanır → genel toplam.
 const laborDecimal=useMemo(()=>moneyDecimal(decimal(v.actual_hours||0).mul(moneyDecimal(v.labor_rate||0))),[v.actual_hours,v.labor_rate]);
 const grandTotal=useMemo(()=>moneyDecimal(laborDecimal.plus(partsSubtotal)),[laborDecimal,partsSubtotal]);
 const save=async()=>{
  try{
   setSaving(true);setError('');
   const payload:any={
    machine_id:v.machine_id,
    customer_id:machineOwnerId??(v.customer_id||null),
    technician_id:v.technician_id,
    // Tam-değiştirme PUT: opened_at kullanıcıya gösterilmese de mevcut değeri
    // olduğu gibi geri gönderilir (create'te null → backend açılışı now yapar).
    opened_at:v.opened_at||null,
    // Tam-değiştirme PUT: planlanan tarih gönderilmezse backend alanı sıfırlar
    // (planlanmış iş emrinde 422). Bu yüzden her zaman taşınır.
    scheduled_date:v.scheduled_date||null,
    priority:v.priority,
    complaint:v.complaint||null,diagnosis:v.diagnosis||null,
    repair_summary:v.repair_summary||null,technician_notes:v.technician_notes||null,
    estimated_hours:Number(v.estimated_hours||0),actual_hours:Number(v.actual_hours||0),labor_rate:Number(v.labor_rate||0),
    warranty_type:v.warranty_type,
    warranty_percent:v.warranty_type==='PARTIAL'?Number(v.warranty_percent||0):null,
   };
   if(!id){
    payload.status=v.status;
    // Para/miktar alanları backend'e Decimal-STRING gider (float ikili gösterim
    // sapmasını önler; backend Pydantic Decimal alanları string'i aynen alır).
    payload.parts=partLines.filter(line=>line.product_id).map(line=>({
     product_id:line.product_id,
     warehouse_id:partWarehouseId,
     quantity:decimalPayload(line.quantity),
     unit_price:decimalPayload(line.unit_price),
     discount:decimalPayload(line.discount),
     tax_rate:decimalPayload(line.tax_rate),
    }));
    if(String(v.work_order_no||'').trim())payload.work_order_no=String(v.work_order_no).trim();
   }
   const response=id?await api.put(`/work-orders/${id}`,payload):await api.post('/work-orders',payload);
   onSaved(response.data);
   onClose();
  }catch(e:any){
   // Backend net Türkçe mesaj döndürür (ör. 409 müşteri/makine uyuşmazlığı,
   // faturalı iş emri, geçersiz teknisyen). Olduğu gibi göster.
   setError(e.response?.data?.detail||e.message);
  }finally{setSaving(false)}
 };
 const needsManualCustomer=Boolean(selectedMachine)&&machineOwnerId==null;
 const needsScheduledDate=v.status==='SCHEDULED'&&!String(v.scheduled_date||'').trim();
 const canSave=Boolean(v.machine_id&&v.technician_id&&effectiveCustomerId)&&!needsScheduledDate&&(v.warranty_type!=='PARTIAL'||(Number(v.warranty_percent)>0&&Number(v.warranty_percent)<100))&&!(!id&&hasZeroPricedPart);
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>{id?'İş Emri Düzenle':'Yeni İş Emri'}</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>{error&&<Alert severity="error">{error}</Alert>}
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   <Autocomplete fullWidth options={machines} loading={machineLoading} noOptionsText={machineOptionsError?MACHINE_OPTIONS_ERROR:'Makine bulunamadı'} loadingText="Makineler aranıyor…" filterOptions={options=>options} getOptionLabel={o=>o?`${o.brand} ${o.model}${o.serial_number?` · ${o.serial_number}`:''}`:''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={selectedMachine} onInputChange={(_,value,reason)=>{if(reason==='input'||reason==='clear')setMachineQuery(value)}} onChange={(_,val)=>setV({...v,machine_id:val?val.id:null,customer_id:null})} renderInput={params=><TextField {...params} required label="Makine"/>}/>
   <Autocomplete fullWidth options={technicians} getOptionLabel={o=>o?.display_name||o?.username||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={technicians.find(t=>t.id===v.technician_id)||null} onChange={(_,val)=>setV({...v,technician_id:val?val.id:null})} renderInput={params=><TextField {...params} required label="Teknisyen"/>}/>
  </Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1} alignItems="center">
   {needsManualCustomer
    ?<Autocomplete fullWidth options={customers} getOptionLabel={o=>o?.name||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={selectedCustomer} onChange={(_,val)=>setV({...v,customer_id:val?val.id:null})} renderInput={params=><TextField {...params} required label="Müşteri (makinenin sahibi tanımlı değil)"/>}/>
    :<TextField fullWidth disabled label="Müşteri" value={selectedCustomer?.name||(selectedMachine?'Makine sahibi tanımlı değil':'Önce makine seçin')}/>}
   <TextField fullWidth select label="Öncelik" value={v.priority} onChange={f('priority')}>{Object.entries(WO_PRIORITY_LABELS).map(([val,label])=><MenuItem key={val} value={val}>{label}</MenuItem>)}</TextField>
  </Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   {!id&&<TextField fullWidth select label="Başlangıç Durumu" value={v.status} onChange={f('status')} helperText={v.status==='SCHEDULED'?'Planlanmış iş emrine parça eklenemez; önce açılması gerekir.':undefined}>
    <MenuItem value="OPEN">Açık</MenuItem>
    <MenuItem value="SCHEDULED">Planlandı</MenuItem>
   </TextField>}
   {(v.status==='SCHEDULED'||v.scheduled_date)&&<TextField fullWidth type="datetime-local" label="Planlanan Tarih" value={v.scheduled_date} onChange={f('scheduled_date')} required={v.status==='SCHEDULED'} InputLabelProps={{shrink:true}} error={needsScheduledDate} helperText={needsScheduledDate?'Planlanmış iş emri için zorunlu':undefined}/>}
  </Stack>
  {!id&&<TextField label="İş Emri No (boş bırakılırsa otomatik üretilir)" value={v.work_order_no} onChange={f('work_order_no')}/>}
  <TextField label="Şikayet" multiline minRows={2} value={v.complaint} onChange={f('complaint')}/>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   <TextField fullWidth label="Teşhis" multiline minRows={2} value={v.diagnosis} onChange={f('diagnosis')}/>
   <TextField fullWidth label="Yapılan İşlem" multiline minRows={2} value={v.repair_summary} onChange={f('repair_summary')}/>
  </Stack>
  <TextField label="Teknisyen Notları" multiline minRows={2} value={v.technician_notes} onChange={f('technician_notes')}/>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   <TextField fullWidth type="number" label="Tahmini Saat" value={v.estimated_hours} onChange={f('estimated_hours')} inputProps={{min:0,step:.25}}/>
   <TextField fullWidth type="number" label="Gerçekleşen Saat" value={v.actual_hours} onChange={f('actual_hours')} inputProps={{min:0,step:.25}}/>
   <TextField fullWidth type="number" label="Saat Ücreti (₺)" value={v.labor_rate} onChange={f('labor_rate')} inputProps={{min:0,step:.01}}/>
  </Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1} alignItems="center">
   <TextField fullWidth select label="Garanti" value={v.warranty_type} onChange={f('warranty_type')}>{Object.entries(WARRANTY_TYPE_LABELS).map(([val,label])=><MenuItem key={val} value={val}>{label}</MenuItem>)}</TextField>
   {v.warranty_type==='PARTIAL'&&<TextField fullWidth type="number" label="Garanti Oranı (%)" value={v.warranty_percent} onChange={f('warranty_percent')} inputProps={{min:0,max:100,step:1}} helperText="0 ile 100 arasında"/>}
   <Typography sx={{whiteSpace:'nowrap',minWidth:160}} color="text.secondary">İşçilik: <b>{new Intl.NumberFormat('tr-TR',{style:'currency',currency:'TRY'}).format(laborTotal)}</b></Typography>
  </Stack>
  {!id&&<><Divider/><Typography variant="h6" fontWeight={800}>Kullanılan Parçalar</Typography>
   {partProductsTruncated&&<Alert severity="warning">Ürün listesi eksik; aramayı daraltın.</Alert>}
   <Autocomplete options={warehouses} getOptionLabel={o=>o?.name||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={warehouses.find(w=>w.id===partWarehouseId)||null} onChange={(_,value)=>{setPartWarehouseId(value?.id||null);setPartLines([blankPartLine()]);setPartQuery('')}} renderInput={params=><TextField {...params} label="Parça Deposu"/>}/>
   {partLines.map((line,index)=>{const product=partProducts.find(item=>item.id===line.product_id)||null;
    // Parça müşteriye SATIŞ fiyatından yansır (alış değil); satış fiyatı
    // tanımsızsa 0 yazılır ve satırda uyarı çıkar, kullanıcı elle girer.
    const missingSale=Boolean(line.product_id)&&!(Number(line.unit_price)>0);
    return <Stack key={index} spacing={0.5}>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1} alignItems={{sm:'center'}}>
     <Autocomplete fullWidth disabled={!partWarehouseId} options={partProducts} filterOptions={options=>options} value={product} isOptionEqualToValue={(o,val)=>o.id===val.id} getOptionLabel={o=>o?`${o.name}${o.product_code?` · ${o.product_code}`:''} · kullanılabilir ${Number(o.available_stock??o.warehouse_stock??0)} ${o.unit||''}`:''} onInputChange={(_,value,reason)=>{if(reason==='input'||reason==='clear')setPartQuery(value)}} onChange={(_,value)=>setPartLines(current=>current.map((item,i)=>i===index?{...item,product_id:value?.id||null,unit_price:Number(value?.sale_price||0)}:item))} renderInput={params=><TextField {...params} label="Parça ara / seç"/>}/>
     <TextField type="number" label="Miktar" value={line.quantity} onChange={event=>setPartLines(current=>current.map((item,i)=>i===index?{...item,quantity:Number(event.target.value)}:item))} sx={{width:{sm:110}}} inputProps={{min:0,step:1}}/>
     <TextField type="number" label="Birim Fiyat (₺)" value={line.unit_price} disabled={!line.product_id} error={missingSale} onChange={event=>setPartLines(current=>current.map((item,i)=>i===index?{...item,unit_price:Number(event.target.value)}:item))} sx={{width:{sm:150}}} inputProps={{min:0,step:.01}}/>
     <Typography sx={{width:{sm:130},textAlign:'right',whiteSpace:'nowrap'}} color="text.secondary">{line.product_id?money(partLineTotalDecimal(line).toFixed(2)):''}</Typography>
     <IconButton aria-label="Parça satırını sil" disabled={partLines.length===1||(index===partLines.length-1&&isLineBlank(line))} onClick={()=>setPartLines(current=>current.filter((_,i)=>i!==index))}><DeleteIcon/></IconButton>
    </Stack>
    {missingSale&&<Alert severity="warning" sx={{py:0}}>Satış fiyatı tanımsız — birim fiyatı elle girin.</Alert>}
   </Stack>})}
   <Divider/>
   <Stack spacing={0.25} sx={{alignItems:'flex-end'}}>
    <Typography color="text.secondary">Parça Ara Toplamı: <b>{money(moneyDecimal(partsSubtotal).toFixed(2))}</b></Typography>
    <Typography color="text.secondary">İşçilik: <b>{money(laborDecimal.toFixed(2))}</b></Typography>
    <Typography variant="h6" fontWeight={800}>Genel Toplam: {money(grandTotal.toFixed(2))}</Typography>
   </Stack>
   {hasZeroPricedPart&&<Alert severity="error">Birim fiyatı 0 olan parça kaydedilemez; satış fiyatını girin.</Alert>}
  </>}
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!canSave}>{saving?'Kaydediliyor...':'Kaydet'}</Button></DialogActions></Dialog>;
}
