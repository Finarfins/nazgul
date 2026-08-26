import {useEffect,useRef,useState} from 'react';
import {Alert,Box,Button,Chip,CircularProgress,Stack,Tab,Typography} from '@mui/material';
import {api,errorDetail,money} from '../api';
import type {components} from '../api/types.gen';

const sourceLabel:Record<string,string>={MANUAL:'Manuel',PURCHASE:'Alış'};
type HistoryItem=components['schemas']['SupplierPriceHistoryItem'];
type HistoryResponse=components['schemas']['SupplierPriceHistoryResponse'];

// ...rest ŞART: MUI <Tabs> çocuklarına onChange/value/selected/indicator
// proplarını enjekte eder. Bunlar Tab'e geçirilmediği sürece sekme görünür ama
// tıklanmaz — bu sarmalayıcı tam olarak o kusuru taşıyordu, yani "Fiyat
// Geçmişi" sekmesi açılamıyordu.
export function SupplierPriceHistoryTab({allowed,...rest}:{allowed:boolean}&Record<string,unknown>){
  return allowed?<Tab label="Fiyat Geçmişi" {...rest}/>:null;
}

export default function SupplierPriceHistory({supplierId}:{supplierId:number}){
  const [items,setItems]=useState<HistoryItem[]>([]);
  const [loading,setLoading]=useState(true);
  const [loadingMore,setLoadingMore]=useState(false);
  const [error,setError]=useState('');
  const [nextCursor,setNextCursor]=useState<string|null>(null);
  const requestGeneration=useRef(0);

  useEffect(()=>{
    const generation=++requestGeneration.current;
    setLoading(true);setLoadingMore(false);setError('');
    api.get<HistoryResponse>('/supplier-prices/history',{params:{supplier_id:supplierId}})
      .then(response=>{if(requestGeneration.current===generation){setItems(response.data.items);setNextCursor(response.data.next_cursor||null)}})
      .catch(err=>{if(requestGeneration.current===generation)setError(errorDetail(err,'Fiyat geçmişi yüklenemedi.'))})
      .finally(()=>{if(requestGeneration.current===generation)setLoading(false)});
    return()=>{requestGeneration.current++};
  },[supplierId]);

  const loadMore=async()=>{
    if(!nextCursor||loadingMore)return;
    const generation=requestGeneration.current;
    try{
      setLoadingMore(true);setError('');
      const response=await api.get<HistoryResponse>('/supplier-prices/history',{params:{supplier_id:supplierId,cursor:nextCursor}});
      if(requestGeneration.current!==generation)return;
      setItems(current=>[...current,...response.data.items]);
      setNextCursor(response.data.next_cursor||null);
    }catch(err){if(requestGeneration.current===generation)setError(errorDetail(err,'Fiyat geçmişinin devamı yüklenemedi.'))}
    finally{if(requestGeneration.current===generation)setLoadingMore(false)}
  };

  if(loading)return <Box py={4} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>;
  if(error)return <Alert severity="error">{error}</Alert>;
  if(items.length===0)return <Typography color="text.secondary">Kayıtlı fiyat geçmişi yok.</Typography>;

  return <Stack spacing={1}>
    {items.map(item=><Stack key={item.id} direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1} sx={{p:1.5,border:'1px solid',borderColor:'divider',borderRadius:2}}>
      <Box>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Typography fontWeight={800}>{item.product_name}</Typography>
          <Chip size="small" variant="outlined" label={sourceLabel[item.source]||item.source}/>
        </Stack>
        <Typography color="text.secondary" fontSize={14}>{String(item.captured_at).slice(0,16).replace('T',' ')}{item.note?` · ${item.note}`:''}</Typography>
      </Box>
      <Box textAlign={{sm:'right'}}>
        <Typography fontWeight={900}>{item.price} {item.currency}</Typography>
        {item.price_in_try!=null&&item.currency!=='TRY'&&<Typography color="text.secondary" fontSize={14}>{money(item.price_in_try)}</Typography>}
      </Box>
    </Stack>)}
    {nextCursor&&<Button variant="outlined" onClick={loadMore} disabled={loadingMore} sx={{alignSelf:'center'}}>{loadingMore?'Yükleniyor...':'Daha Fazla Yükle'}</Button>}
  </Stack>;
}
