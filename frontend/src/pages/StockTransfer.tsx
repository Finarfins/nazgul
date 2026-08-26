import {useCallback,useEffect,useState} from 'react';
import {Alert,Autocomplete,Box,Chip,Paper,Stack,TextField,Typography,useTheme} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import {Link as RouterLink,useNavigate} from 'react-router-dom';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import TransferDialog from '../components/TransferDialog';

type ProductOption={id:number;name:string;product_code:string|null;unit?:string};
type WarehouseRow={
  warehouse_id:number;warehouse_name:string;warehouse_code:string|null;
  is_default:boolean;branch_id:number|null;branch_name:string|null;
  quantity:string|number;critical_stock:string|number;
};
type StockView={
  product:{id:number;name:string;product_code:string|null;unit:string|null};
  warehouses:WarehouseRow[];total_quantity:string|number;available_warehouse_count:number;
};
type TransferRow={
  id:number;transfer_date:string;source_name:string;target_name:string;
  note:string|null;status:string;item_count:number;total_quantity:string|number;
};

const label=(p:ProductOption|null)=>p?`${p.product_code?p.product_code+' — ':''}${p.name}`:'';
const num=(value:string|number)=>Number(value??0);

// Şubeler Arası Parça Transferi: önce parça seçilir, hangi şubede kaç adet
// olduğu görülür, sonra stoğu olan şubeden transfer başlatılır. Stok hareketi
// mevcut POST /warehouses/transfers uç noktasıyla yapılır (ikinci bir mekanizma
// yok). Renk/ölçüler merkezi temadan gelir (Sungur standardı).
export default function StockTransfer(){
  const theme=useTheme();
  const navigate=useNavigate();
  const {can}=useAuth();
  const canTransfer=can('stock');
  const [products,setProducts]=useState<ProductOption[]>([]);
  const [product,setProduct]=useState<ProductOption|null>(null);
  const [view,setView]=useState<StockView|null>(null);
  const [transfers,setTransfers]=useState<TransferRow[]>([]);
  const [loading,setLoading]=useState(false);
  const [dialogSource,setDialogSource]=useState<number|null>(null);
  const [error,setError]=useState('');

  useEffect(()=>{
    api.get('/products',{params:{limit:2000}}).then(r=>setProducts(r.data)).catch(()=>setProducts([]));
  },[]);

  const loadTransfers=useCallback(()=>{
    api.get('/warehouses/transfers')
      .then(r=>setTransfers(r.data))
      .catch(err=>setError(errorDetail(err,'Transfer geçmişi yüklenemedi.')));
  },[]);
  useEffect(()=>{loadTransfers()},[loadTransfers]);

  const loadStock=useCallback(()=>{
    if(!product){setView(null);return;}
    setLoading(true);
    api.get(`/products/${product.id}/warehouse-stock`)
      .then(r=>setView(r.data))
      .catch(err=>{setView(null);setError(errorDetail(err,'Depo stokları yüklenemedi.'))})
      .finally(()=>setLoading(false));
  },[product]);
  useEffect(()=>{loadStock()},[loadStock]);

  const warehouses=(view?.warehouses??[]).map(row=>({id:row.warehouse_id,name:row.warehouse_name}));
  const unit=view?.product.unit||product?.unit||'';
  // TransferDialog models optional fields as ``string|undefined``; the API
  // returns SQL nulls, so normalise rather than widening the dialog's contract.
  const dialogProduct=product
    ?{id:product.id,name:product.name,product_code:product.product_code??undefined,unit:product.unit??undefined}
    :null;

  const done=()=>{setDialogSource(null);loadStock();loadTransfers();};
  const stockColumns:GridColDef[]=[
    {field:'warehouse_name',headerName:'Depo / Şube',minWidth:220,flex:1,renderCell:({row})=><Box><RouterLink to={`/depolar/${row.warehouse_id}`} style={{color:'inherit',fontWeight:700,textDecoration:'none'}}>{row.warehouse_name}</RouterLink>{row.warehouse_code&&<Chip label={row.warehouse_code} size="small" variant="outlined" sx={{ml:1}}/>}{row.is_default&&<Chip label="Merkez" size="small" color="primary" variant="outlined" sx={{ml:1}}/>}{row.branch_name&&<Typography variant="body2" color="text.secondary">{row.branch_name}</Typography>}</Box>},
    {field:'quantity',headerName:'Mevcut',width:130,renderCell:({row})=>{const quantity=num(row.quantity);const critical=num(row.critical_stock);return <Typography fontWeight={800} color={quantity<=0?'text.secondary':critical>0&&quantity<=critical?'warning.main':'success.main'}>{quantity} {unit}</Typography>}},
    {field:'critical_stock',headerName:'Kritik',width:110,valueFormatter:value=>num(value)||'—'},
  ];
  const transferColumns:GridColDef[]=[
    {field:'transfer_date',headerName:'Tarih',width:140,renderCell:({row})=><RouterLink to={`/depo-transferleri/${row.id}`} style={{color:'inherit',fontWeight:700,textDecoration:'none'}}>{row.transfer_date}</RouterLink>},
    {field:'source_name',headerName:'Kaynak',minWidth:160,flex:1},
    {field:'target_name',headerName:'Hedef',minWidth:160,flex:1},
    {field:'item_count',headerName:'Kalem',width:90},
    {field:'total_quantity',headerName:'Toplam Miktar',width:140,valueFormatter:value=>num(value)},
    {field:'note',headerName:'Not',minWidth:180,flex:1,valueFormatter:value=>value||'—'},
  ];

  return <Box sx={{'@media (max-width:899.95px)':{'& .MuiButton-root':{minHeight:44},'& .MuiIconButton-root':{minWidth:44,minHeight:44},'& .MuiInputBase-input':{minHeight:44,boxSizing:'border-box'}}}}>
    <Typography variant="h5" fontWeight={700} mb={.5}>Şubeler Arası Transfer</Typography>
    <Typography color="text.secondary" mb={2}>Parçayı seçin, hangi şubede stok olduğunu görün ve stoğu olan şubeden transfer başlatın.</Typography>

    {error&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError('')}>{error}</Alert>}

    <Paper variant="outlined" sx={{p:'18px',mb:2,borderRadius:`${theme.shape.borderRadius}px`}}>
      <Autocomplete sx={{maxWidth:480}} options={products} value={product}
        isOptionEqualToValue={(a,b)=>a.id===b.id} getOptionLabel={label}
        onChange={(_,v)=>{setProduct(v);setError('')}}
        renderInput={p=><TextField {...p} label="Parça ara (ad / kod)" size="small"/>}/>
      {view&&<Stack direction="row" spacing={1} mt={1.5} flexWrap="wrap" useFlexGap>
        <Chip label={`Toplam stok: ${num(view.total_quantity)} ${unit}`.trim()} color="primary" variant="outlined"/>
        <Chip label={`Stoğu olan şube: ${view.available_warehouse_count}`}
          color={view.available_warehouse_count>0?'success':'warning'} variant="outlined"/>
      </Stack>}
    </Paper>

    {product&&<Box data-testid="stock-transfer-stock-data-surface" sx={{mb:3}}>
      <ResponsiveTable
        rows={view?.warehouses??[]}
        columns={stockColumns}
        loading={loading}
        cardTitle={row=>row.warehouse_name}
        cardSubtitle={row=>[row.warehouse_code,row.branch_name].filter(Boolean).join(' · ')}
        cardFields={[
          {label:'Mevcut',value:row=>`${num(row.quantity)} ${unit}`.trim()},
          {label:'Kritik',value:row=>num(row.critical_stock)||'—'},
          {label:'Merkez',value:row=>row.is_default?'Evet':'Hayır'},
        ]}
        cardActions={canTransfer?[{label:'Bu depodan gönder',ariaLabel:()=> 'Bu depodan gönder',icon:<LocalShippingIcon/>,disabled:row=>num(row.quantity)<=0,onClick:row=>setDialogSource(row.warehouse_id)}]:undefined}
        cardActionsOnDesktop
        getRowId={row=>row.warehouse_id}
        onRowClick={row=>navigate(`/depolar/${row.warehouse_id}`)}
      />
    </Box>}

    <Typography variant="h6" fontWeight={700} mb={1}>Transfer Geçmişi</Typography>
    <Box data-testid="stock-transfer-history-data-surface">
      <ResponsiveTable
        rows={transfers}
        columns={transferColumns}
        cardTitle={row=>row.transfer_date}
        cardSubtitle={row=>`${row.source_name} → ${row.target_name}`}
        cardFields={[
          {label:'Kalem',value:row=>row.item_count},
          {label:'Toplam miktar',value:row=>num(row.total_quantity)},
          {label:'Not',value:row=>row.note||'—'},
        ]}
        getRowId={row=>row.id}
        onRowClick={row=>navigate(`/depo-transferleri/${row.id}`)}
      />
    </Box>

    {/* Reuses the existing transfer dialog so there is exactly one write path. */}
    <TransferDialog open={dialogSource!==null} warehouses={warehouses}
      initialSourceId={dialogSource} initialProduct={dialogProduct}
      onClose={()=>setDialogSource(null)} onDone={done}/>
  </Box>;
}
