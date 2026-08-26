import {type PointerEvent,useState} from 'react';
import BackspaceOutlinedIcon from '@mui/icons-material/BackspaceOutlined';
import CloseIcon from '@mui/icons-material/Close';
import KeyboardCapslockIcon from '@mui/icons-material/KeyboardCapslock';
import KeyboardReturnIcon from '@mui/icons-material/KeyboardReturn';
import SpaceBarIcon from '@mui/icons-material/SpaceBar';
import {Box,Button,IconButton,Paper,Stack,Typography} from '@mui/material';

export type OnScreenKeyboardMode='numeric'|'text';

type Props={
 mode:OnScreenKeyboardMode;
 value:string;
 activeLabel:string;
 replaceOnNextKey?:boolean;
 onChange:(value:string)=>void;
 onEnter?:()=>void;
 onClose:()=>void;
};

// Parça kodları ve barkodlar harf+rakam karışık ("hffh - 23112", OEM 84348882).
// Sayı satırı olmadan bunlar ekran klavyesiyle hiç aranamıyordu.
const TEXT_ROWS=[
 ['1','2','3','4','5','6','7','8','9','0'],
 ['Q','W','E','R','T','Y','U','I','O','P','Ğ','Ü'],
 ['A','S','D','F','G','H','J','K','L','Ş','İ'],
 ['Z','X','C','V','B','N','M','Ö','Ç'],
];
const NUMERIC_ROWS=[['7','8','9'],['4','5','6'],['1','2','3'],['0',',','.']];

const lower=(key:string)=>key.toLocaleLowerCase('tr-TR');

export default function OnScreenKeyboard({
 mode,
 value,
 activeLabel,
 replaceOnNextKey=false,
 onChange,
 onEnter,
 onClose,
}:Props){
 const [uppercase,setUppercase]=useState(false);
 const press=(rawKey:string)=>{
  const key=mode==='text'&&!uppercase?lower(rawKey):rawKey;
  if(mode==='numeric'&&(key===','||key==='.')){
   if(!replaceOnNextKey&&(value.includes(',')||value.includes('.')))return;
   onChange(replaceOnNextKey?`0${key}`:`${value}${key}`);
   return;
  }
  onChange(mode==='numeric'&&replaceOnNextKey?key:`${value}${key}`);
 };
 const preventFocusLoss=(event:PointerEvent)=>event.preventDefault();
 const enter=()=>{onEnter?.();onClose()};
 const keyButton=(key:string)=>(
  <Button
   key={key}
   variant="outlined"
   aria-label={`Tuş ${mode==='text'&&!uppercase?lower(key):key}`}
   onPointerDown={preventFocusLoss}
   onClick={()=>press(key)}
   sx={{minWidth:mode==='text'?44:0,minHeight:48,fontSize:mode==='numeric'?'1.35rem':'1rem',fontWeight:800,px:1}}
  >
   {mode==='text'&&!uppercase?lower(key):key}
  </Button>
 );
 return <Paper
  role="region"
  aria-label="Ekran klavyesi"
  elevation={12}
  sx={{
   position:'fixed',
   zIndex:theme=>theme.zIndex.modal-1,
   left:{xs:8,md:'50%'},
   right:{xs:8,md:'auto'},
   bottom:8,
   transform:{md:'translateX(-50%)'},
   width:{md:mode==='numeric'?420:920},
   maxWidth:'calc(100vw - 16px)',
   maxHeight:'48dvh',
   overflowY:'auto',
   border:'1px solid',
   borderColor:'divider',
   borderRadius:3,
   p:{xs:1,sm:1.5},
  }}
 >
  <Stack spacing={1}>
   <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
    <Box minWidth={0}>
     <Typography fontWeight={800} noWrap>{mode==='numeric'?'Sayısal tuş takımı':'Türkçe klavye'}</Typography>
     <Typography variant="caption" color="text.secondary" noWrap>Aktif alan: {activeLabel}</Typography>
    </Box>
    <IconButton
     aria-label="Ekran klavyesini kapat"
     onPointerDown={preventFocusLoss}
     onClick={onClose}
     sx={{minWidth:44,minHeight:44}}
    >
     <CloseIcon/>
    </IconButton>
   </Stack>
   {mode==='numeric'
    ?<Stack spacing={.75}>
      {NUMERIC_ROWS.map((row,index)=><Box key={index} sx={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:.75}}>{row.map(keyButton)}</Box>)}
      <Box sx={{display:'grid',gridTemplateColumns:'1.2fr 1fr 1.2fr',gap:.75}}>
       <Button color="warning" variant="outlined" onPointerDown={preventFocusLoss} onClick={()=>onChange('')} sx={{minHeight:48,fontWeight:800}}>Temizle</Button>
       <Button variant="outlined" aria-label="Sil" onPointerDown={preventFocusLoss} onClick={()=>onChange(value.slice(0,-1))} sx={{minHeight:48}}><BackspaceOutlinedIcon/></Button>
       <Button variant="contained" aria-label="Enter" onPointerDown={preventFocusLoss} onClick={enter} sx={{minHeight:48}}><KeyboardReturnIcon/></Button>
      </Box>
     </Stack>
    :<Stack spacing={.75}>
      {TEXT_ROWS.map((row,index)=><Box key={index} sx={{display:'grid',gridTemplateColumns:`repeat(${row.length},minmax(44px,1fr))`,gap:{xs:.35,sm:.6},overflowX:'auto',pb:.25}}>{row.map(keyButton)}</Box>)}
      <Box sx={{display:'grid',gridTemplateColumns:'1fr 4fr 1fr 1fr',gap:.75}}>
       <Button
        variant={uppercase?'contained':'outlined'}
        aria-label="Büyük küçük harf"
        aria-pressed={uppercase}
        onPointerDown={preventFocusLoss}
        onClick={()=>setUppercase(current=>!current)}
        sx={{minWidth:44,minHeight:48}}
       >
        <KeyboardCapslockIcon/>
       </Button>
       <Button variant="outlined" aria-label="Boşluk" onPointerDown={preventFocusLoss} onClick={()=>press(' ')} sx={{minHeight:48}}><SpaceBarIcon/></Button>
       <Button variant="outlined" aria-label="Sil" onPointerDown={preventFocusLoss} onClick={()=>onChange(value.slice(0,-1))} sx={{minWidth:44,minHeight:48}}><BackspaceOutlinedIcon/></Button>
       <Button variant="contained" aria-label="Enter" onPointerDown={preventFocusLoss} onClick={enter} sx={{minWidth:44,minHeight:48}}><KeyboardReturnIcon/></Button>
      </Box>
     </Stack>}
  </Stack>
 </Paper>;
}
