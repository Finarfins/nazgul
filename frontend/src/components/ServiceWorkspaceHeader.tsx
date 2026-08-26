import type {ReactNode} from 'react';
import {Box,Button,Stack,Typography} from '@mui/material';
import AgricultureIcon from '@mui/icons-material/Agriculture';
import BuildIcon from '@mui/icons-material/Build';
import {useLocation,useNavigate} from 'react-router-dom';

type Stat={label:string;value:string|number;tone?:'default'|'warning'};

export default function ServiceWorkspaceHeader({title,description,action,stats}:{title:string;description:string;action?:ReactNode;stats:Stat[]}){
 const nav=useNavigate();const location=useLocation();
 const sections=[{label:'Makine Parkı',path:'/makineler',icon:<AgricultureIcon/>},{label:'İş Emirleri',path:'/is-emirleri',icon:<BuildIcon/>}];
 return <Box sx={{
  position:'relative',
  overflow:'hidden',
  borderRadius:3.5,
  p:{xs:2.25,md:3},
  color:'#fff',
  background:'linear-gradient(125deg, #071e41 0%, #0b3567 68%, #164a8a 100%)',
  boxShadow:'0 18px 44px rgba(7,30,65,.16)',
  '&:after':{
   content:'""',
   position:'absolute',
   width:250,
   height:250,
   borderRadius:'50%',
   right:-70,
   top:-145,
   bgcolor:'rgba(255,255,255,.07)',
  },
 }}>
  <Stack position="relative" zIndex={1} spacing={2.25}>
   <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" alignItems={{xs:'flex-start',md:'center'}} gap={2}>
    <Box><Typography variant="caption" fontWeight={850} letterSpacing=".12em" sx={{color:'#f5c400'}}>SERVİS OPERASYON MERKEZİ</Typography><Typography variant="h4" fontWeight={950} sx={{fontSize:{xs:25,sm:31}}}>{title}</Typography><Typography sx={{color:'rgba(255,255,255,.72)',mt:.25}}>{description}</Typography></Box>
    {action}
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} gap={1.25} alignItems={{sm:'center'}} justifyContent="space-between">
    <Stack direction="row" gap={0.75} sx={{overflowX:'auto'}}>
     {sections.map(section=>{const active=location.pathname===section.path||location.pathname.startsWith(`${section.path}/`);return <Button key={section.path} startIcon={section.icon} variant={active?'contained':'text'} onClick={()=>nav(section.path)} sx={{flexShrink:0,color:active?'#071e41':'#fff',bgcolor:active?'#f5c400':'rgba(255,255,255,.1)','&:hover':{bgcolor:active?'#ffd329':'rgba(255,255,255,.18)'}}}>{section.label}</Button>})}
    </Stack>
    <Stack direction="row" gap={1} flexWrap="wrap">{stats.map(stat=><Box key={stat.label} sx={{minWidth:112,px:1.5,py:.9,borderRadius:2,bgcolor:stat.tone==='warning'?'rgba(245,196,0,.16)':'rgba(255,255,255,.1)',border:'1px solid',borderColor:stat.tone==='warning'?'rgba(245,196,0,.45)':'rgba(255,255,255,.14)'}}><Typography fontSize={11} fontWeight={800} letterSpacing=".06em" sx={{color:'rgba(255,255,255,.62)'}}>{stat.label.toUpperCase()}</Typography><Typography fontSize={20} fontWeight={950} color={stat.tone==='warning'?'#f5c400':'#fff'}>{stat.value}</Typography></Box>)}</Stack>
   </Stack>
  </Stack>
 </Box>;
}
