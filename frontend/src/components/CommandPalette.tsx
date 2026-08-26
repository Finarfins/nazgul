import React from 'react';
import {useEffect,useMemo,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Avatar,Box,Chip,Dialog,DialogContent,InputAdornment,List,ListItemAvatar,ListItemButton,ListItemText,Stack,TextField,Typography} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import ShoppingCartCheckoutIcon from '@mui/icons-material/ShoppingCartCheckout';
import PersonSearchIcon from '@mui/icons-material/PersonSearch';
import Inventory2Icon from '@mui/icons-material/Inventory2';
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import AnalyticsIcon from '@mui/icons-material/Analytics';
import {api} from '../api';
import {useAuth} from '../AuthContext';
import {permissionForPath,type Permission} from '../navigation';

type Result={type:string;id:number;title:string;subtitle:string;path:string};
type Props={open:boolean;onClose:()=>void};

// `permission` yalnız kayıt OLUŞTURAN kısayollarda verilir: hedef sayfayı
// açmak `read` yeterken, o sayfada yeni belge açmak yazma izni ister. Diğer
// eylemler iznini `navigation.ts`'teki tek kaynaktan, hedef rotadan alır.
const actions=[
 {title:'Yeni satış oluştur',subtitle:'Satış formunu aç',path:'/satislar?new=1',permission:'sales',icon:<AddShoppingCartIcon/>},
 {title:'Yeni alış oluştur',subtitle:'Alış formunu aç',path:'/alislar?new=1',permission:'purchases',icon:<ShoppingCartCheckoutIcon/>},
 {title:'Yeni fiziksel sayım',subtitle:'Çok satırlı depo sayımı başlat',path:'/stok-sayimlari?new=1',permission:'stock',icon:<FactCheckOutlinedIcon/>},
 {title:'Stok sayımlarını aç',subtitle:'Sayım belgeleri ve depo farkları',path:'/stok-sayimlari',icon:<FactCheckOutlinedIcon/>},
 {title:'Müşterileri aç',subtitle:'Cari hesap listesi',path:'/musteriler',icon:<PersonSearchIcon/>},
 {title:'Ürünleri aç',subtitle:'Ürün ve stok yönetimi',path:'/urunler',icon:<Inventory2Icon/>},
 {title:'Raporları aç',subtitle:'Satış ve finans raporları',path:'/raporlar',icon:<ReceiptLongIcon/>},
 {title:'Akıllı analizleri aç',subtitle:'Hazır işletme önerileri',path:'/analizler',icon:<AnalyticsIcon/>},
] satisfies {title:string;subtitle:string;path:string;permission?:Permission;icon:React.ReactNode}[];

const typeLabel:Record<string,string>={customer:'Müşteri',product:'Ürün',sale:'Satış',purchase:'Alış'};
const typeIcon:Record<string,React.ReactNode>={customer:<PersonSearchIcon/>,product:<Inventory2Icon/>,sale:<ReceiptLongIcon/>,purchase:<ShoppingCartCheckoutIcon/>};

export default function CommandPalette({open,onClose}:Props){
 const nav=useNavigate();const {can}=useAuth();const [q,setQ]=useState('');const [items,setItems]=useState<Result[]>([]);const [loading,setLoading]=useState(false);const input=useRef<HTMLInputElement|null>(null);
 // Palet, menüyle aynı tek-kaynak izin haritasını kullanır: kullanıcının
 // açamayacağı bir sayfa burada da listelenmez (hem hızlı işlemler hem arama
 // sonuçları için).
 useEffect(()=>{if(open){setQ('');setItems([]);setTimeout(()=>input.current?.focus(),50)}},[open]);
 useEffect(()=>{if(!open||q.trim().length<2){setItems([]);return}const ctrl=new AbortController();const timer=setTimeout(async()=>{setLoading(true);try{const r=await api.get('/search',{params:{q:q.trim(),limit:8},signal:ctrl.signal});setItems(r.data.items||[])}catch(e:any){if(e?.code!=='ERR_CANCELED')setItems([])}finally{setLoading(false)}},180);return()=>{clearTimeout(timer);ctrl.abort()}},[q,open]);
 const filteredActions=useMemo(()=>{const s=q.trim().toLocaleLowerCase('tr-TR');const permitted=actions.filter(a=>can(a.permission??permissionForPath(a.path)));return !s?permitted:permitted.filter(a=>(a.title+' '+a.subtitle).toLocaleLowerCase('tr-TR').includes(s))},[q,can]);
 const visibleItems=useMemo(()=>items.filter(item=>can(permissionForPath(item.path))),[items,can]);
 const go=(path:string)=>{onClose();nav(path)};
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" PaperProps={{sx:{position:'fixed',top:{xs:8,sm:56},m:0,borderRadius:3,overflow:'hidden'}}}>
  <DialogContent sx={{p:0}}>
   <TextField inputRef={input} value={q} onChange={e=>setQ(e.target.value)} placeholder="Müşteri, ürün, belge veya komut ara..." fullWidth autoComplete="off" onKeyDown={e=>{if(e.key==='Escape')onClose()}} InputProps={{startAdornment:<InputAdornment position="start"><SearchIcon/></InputAdornment>,endAdornment:<InputAdornment position="end"><Chip size="small" label="Ctrl K"/></InputAdornment>,sx:{borderRadius:0,fontSize:17,py:.7}}}/>
   <Box sx={{maxHeight:'62vh',overflowY:'auto'}}>
    {filteredActions.length>0&&<><Typography variant="overline" color="text.secondary" sx={{display:'block',px:2,pt:1.5}}>Hızlı işlemler</Typography><List dense>{filteredActions.map(a=><ListItemButton key={a.path} onClick={()=>go(a.path)}><ListItemAvatar><Avatar sx={{bgcolor:'secondary.main',width:34,height:34}}>{a.icon}</Avatar></ListItemAvatar><ListItemText primary={a.title} secondary={a.subtitle}/></ListItemButton>)}</List></>}
    {q.trim().length>=2&&<><Typography variant="overline" color="text.secondary" sx={{display:'block',px:2,pt:1}}>Arama sonuçları {loading?'· aranıyor':''}</Typography><List dense>{visibleItems.map(item=><ListItemButton key={`${item.type}-${item.id}`} onClick={()=>go(item.path)}><ListItemAvatar><Avatar sx={{bgcolor:'primary.main',width:34,height:34}}>{typeIcon[item.type]}</Avatar></ListItemAvatar><ListItemText primary={<Stack direction="row" gap={1} alignItems="center"><span>{item.title}</span><Chip size="small" label={typeLabel[item.type]||item.type}/></Stack>} secondary={item.subtitle}/></ListItemButton>)}{!loading&&visibleItems.length===0&&<Box px={2} py={3} textAlign="center"><Typography color="text.secondary">Sonuç bulunamadı</Typography></Box>}</List></>}
   </Box>
  </DialogContent>
 </Dialog>
}