import React from 'react';
import {useCallback,useEffect,useMemo,useState} from 'react';
import {NavLink,Outlet,useLocation,useNavigate} from 'react-router-dom';
import {alpha} from '@mui/material/styles';
import {Alert,AppBar,Avatar,Badge,Box,Button,Collapse,Drawer,FormControl,IconButton,InputLabel,List,ListItemButton,ListItemIcon,ListItemText,Menu,MenuItem,Select,Snackbar,Toolbar,Tooltip,Typography,useMediaQuery,useTheme} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import SearchIcon from '@mui/icons-material/Search';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import NotificationsIcon from '@mui/icons-material/Notifications';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import PointOfSaleIcon from '@mui/icons-material/PointOfSale';
import MoreVertIcon from '@mui/icons-material/MoreVert';

import {useAuth} from '../AuthContext';
import {useAppTheme} from '../ThemeContext';
import {sungurTokens} from '../theme';
import {api} from '../api';
import {NAV_GROUPS,NAV_LABELS,PINNED_ITEMS,groupIdForPath,permissionForPath,type NavItem} from '../navigation';

import CommandPalette from './CommandPalette';
import HarmanBrandMark from './HarmanBrandMark';

const drawerWidth=252;
type Notice={title:string;link:string;severity:string};

export default function AppShell(){
 const theme=useTheme();const mobile=useMediaQuery(theme.breakpoints.down('md'));
 const [open,setOpen]=useState(false);const [anchor,setAnchor]=useState<null|HTMLElement>(null);
 const [noticeAnchor,setNoticeAnchor]=useState<null|HTMLElement>(null);const [notices,setNotices]=useState<Notice[]>([]);
 // Dar ekranda tema + bildirim tek bir taşma menüsüne iner (bkz. aşağıdaki
 // araç çubuğu yorumu): iki 44px düğme yerine bir tane yer tutar.
 const [moreAnchor,setMoreAnchor]=useState<null|HTMLElement>(null);
 const [paletteOpen,setPaletteOpen]=useState(false);
 const [logoutError,setLogoutError]=useState('');
 const {user,logout,can,companies,activeCompany,setActiveCompany}=useAuth();
 const {mode,toggle}=useAppTheme();const nav=useNavigate();const location=useLocation();

 // Menü izinleri navigation.ts'teki tek kaynaktan okunur; burada izin yazılmaz.
 const allowed=useCallback((item:NavItem)=>can(permissionForPath(item.path)),[can]);
 const pinned=useMemo(()=>PINNED_ITEMS.filter(allowed),[allowed]);
 // Bir grup, o rolde görünür en az bir maddesi varsa görünür.
 const groups=useMemo(()=>NAV_GROUPS
   .map(group=>({...group,items:group.items.filter(allowed)}))
   .filter(group=>group.items.length>0),[allowed]);

 // Firma adının AYIRT EDİCİ kısmını üretir (firma id -> kısa etiket).
 //
 // Dar ekranda seçici kısa ve MUI sondan kırpıyor: benzer önekli adlarda
 // ekranda kalan kısım TAM OLARAK ortak önek oluyor, farklı olan kısım yok
 // oluyordu. Ortak önek atılınca farklı karakterler ilk sıraya geçer ve
 // kırpma olsa bile okunur kalır.
 //
 // Ölçüt TÜM firmaların ortak öneki DEĞİL, her firmanın EN BENZER kardeşiyle
 // ayrıştığı noktadır; geriye o kardeşten ayrışan SONEK kalır. Global önek
 // yetmiyordu: "… Nakliyat" ile "… Nakliyat 2" global önek atıldıktan sonra da
 // uzun bir ortak parça taşıyıp kırpılınca yine aynı görünüyordu.
 //
 // Tek firma varsa ayırt etme sorunu yoktur, ad olduğu gibi gösterilir.
 //
 // Fonksiyon değil VERİ döndürür: `useMemo`'dan closure döndürmek React
 // Compiler'ın mevcut memoizasyonu koruyamamasına ve lint hatasına yol açıyor.
 const companyLabels=useMemo(()=>{
  const shared=(a:string,b:string)=>{let i=0;while(i<a.length&&i<b.length&&a[i]===b[i])i++;return i};
  const labels=new Map<number,string>();
  for(const company of companies){
   if(companies.length<2){labels.set(company.id,company.name);continue}
   let common=0;
   for(const other of companies)if(other.id!==company.id)common=Math.max(common,shared(company.name,other.name));
   // Kelime ortasından kesmek okunaksız olur: ayrışma noktası son boşluğa
   // yuvarlanır, yani sonek daima bir kelime başından başlar.
   const cut=company.name.lastIndexOf(' ',Math.max(0,common-1))+1;
   labels.set(company.id,cut>=4&&company.name.length>cut?`…${company.name.slice(cut)}`:company.name);
  }
  return labels;
 },[companies]);

 // Bildirim maddeleri iki menüde de kullanılır: masaüstünde kendi zil
 // düğmesinde, dar ekranda taşma menüsünün içinde.
 const noticeItems=notices.length===0
  ?[<MenuItem key="notice-empty" disabled>Yeni bildirim yok</MenuItem>]
  :notices.map((notice,index)=><MenuItem key={`notice-${index}`} onClick={()=>{setNoticeAnchor(null);setMoreAnchor(null);nav(notice.link)}} sx={{whiteSpace:'normal'}}>{notice.title}</MenuItem>);

 // `openGroups` YALNIZ kullanıcının elle açtığı grupları tutar; aktif grup bu
 // kümeye hiç yazılmaz. Görünürlük ikisinin birleşiminden TÜRETİLİR:
 //
 //   expanded = kullanıcı açtı  VEYA  grup aktif sayfayı içeriyor
 //
 // Böylece aktif grup hiçbir durumda kapalı görünemez. Kullanıcı aktif grubu
 // "kapatmayı" denerse yalnız kümeden düşer, görünürlük aktiflikten devam
 // eder; başka bir gruba geçildiğinde kendiliğinden kapanmış olur. Elle açılan
 // diğer gruplar AppShell'in ömrü boyunca (sayfa değişse de) açık kalır.
 //
 // Bu türetme bir effect'in yerini alır: durum senkronizasyonu yok, dolayısıyla
 // "aktif grup kapalı" ara durumu da yok.
 //
 // `toggleGroup` yalnız başlığın yanındaki ok düğmesinden çağrılır; başlığın
 // kendisi gezinir (bkz. `landing`).
 const activeGroup=groupIdForPath(location.pathname);
 const [openGroups,setOpenGroups]=useState<ReadonlySet<string>>(()=>new Set());
 const toggleGroup=(id:string)=>setOpenGroups(current=>{
  const next=new Set(current);
  // Aktif gruba tıklamak onu kümeye ASLA EKLEMEZ, yalnız çıkarır. Aksi hâlde
  // "kapat" niyetiyle yapılan tıklama grubu kalıcı olarak "elle açık" hâline
  // getirir ve kullanıcı başka gruba geçtiğinde de açık kalırdı. Bu dalla
  // görsel olarak açık kalır (aktiflikten), ama aktiflik bitince kapanır.
  if(id===activeGroup)next.delete(id);
  else if(next.has(id))next.delete(id);
  else next.add(id);
  return next;
 });

 useEffect(()=>{api.get('/notifications').then(r=>setNotices(r.data.items||[])).catch(()=>setNotices([]))},[activeCompany?.id]);
 useEffect(()=>{const key=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();setPaletteOpen(true)}};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key)},[]);
 const doLogout=async()=>{try{await logout();nav('/giris')}catch(error){setLogoutError(error instanceof Error?error.message:'Çıkış tamamlanamadı; tekrar deneyin.')}};

 const canPos=can(permissionForPath('/hizli-satis'));

 const itemSx={
  my:.25,minHeight:42,borderRadius:2,color:alpha('#e8eef7',.72),
  '&.active':{
   bgcolor:alpha(theme.palette.primary.main,.18),color:'#fff',
   '&:before':{content:'""',position:'absolute',left:0,width:3,height:20,borderRadius:2,bgcolor:theme.palette.primary.light},
  },
  '&:hover':{bgcolor:alpha('#ffffff',.06),color:'#fff'},
 } as const;

 const renderItem=(item:NavItem,nested=false)=>
  <ListItemButton key={item.path} component={NavLink} to={item.path} end={item.path==='/'} onClick={()=>setOpen(false)}
   sx={{...itemSx,...(nested?{pl:3.5,minHeight:40}:null)}}>
   <ListItemIcon sx={{color:'inherit',minWidth:nested?32:38,'& .MuiSvgIcon-root':{fontSize:nested?18:20}}}>{item.icon}</ListItemIcon>
   <ListItemText primary={item.label} slotProps={{primary:{fontSize:nested?13:13.5,fontWeight:nested?500:600}}}/>
  </ListItemButton>;

 const drawer=<Box sx={{height:'100%',bgcolor:'sidebar',color:'white',display:'flex',flexDirection:'column'}}>
  <Toolbar sx={{minHeight:'82px!important',px:2.25,gap:1.25}}>
   <HarmanBrandMark size={48}/>
   <Box>
    <Typography fontSize={16.5} fontWeight={900} lineHeight={1.05} letterSpacing=".015em">SUNGUR TARIM</Typography>
    <Typography variant="caption" sx={{display:'block',mt:.55,color:'#f5c400',fontSize:9.5,fontWeight:800,letterSpacing:'.13em'}}>HASAT • SERVİS • PARÇA</Typography>
   </Box>
  </Toolbar>
  <Typography variant="caption" sx={{px:2.5,pt:1.5,pb:1,color:alpha('#ffffff',.4),letterSpacing:'.09em'}}>{NAV_LABELS.workspace}</Typography>
  <List sx={{px:1.25,py:0,overflowY:'auto'}}>
   {pinned.map(item=>renderItem(item))}
   {groups.map(group=>{
    const expanded=openGroups.has(group.id)||activeGroup===group.id;
    // Grup başlığı TEK TIKTA grubun ana sayfasına götürür: bu, o roldeki ilk
    // görünür maddedir (Müşteriler→Müşteri Listesi, Servis→İş Emirleri...).
    // Gezinmenin kendisi grubu aktif yapar, açıklık da aktiflikten TÜRETİLİR;
    // bu yüzden başlığın ayrıca `openGroups`'a yazması gerekmez — "elle açık"
    // kümesi kirlenmez, aktiflik bitince grup kendiliğinden kapanır.
    const landing=group.items[0].path;
    return <Box key={group.id}>
     {/* `data-nav-group` testlerin grup başlıklarını kesin olarak seçmesi
         içindir: ileride kenar çubuğuna başka bir accordion ya da profil
         menüsü eklenirse bu sayım bozulmasın. Açma/kapama ayrı bir ok
         düğmesindedir (`data-nav-toggle`): başlık gezinir, ok daraltır —
         iç içe düğme (a > button) üretmemek için de kardeş çizilirler. */}
     <Box sx={{display:'flex',alignItems:'center'}}>
      <ListItemButton data-nav-group={group.id} onClick={()=>{setOpen(false);nav(landing)}} sx={{...itemSx,mt:.75,flexGrow:1,minWidth:0}}>
       <ListItemIcon sx={{color:'inherit',minWidth:38,'& .MuiSvgIcon-root':{fontSize:20}}}>{group.icon}</ListItemIcon>
       <ListItemText primary={group.label} slotProps={{primary:{fontSize:13.5,fontWeight:700}}}/>
      </ListItemButton>
      {/* Mobil çekmecede de dokunulabilir kalması için 44px hedef; gezinme
          YAPMAZ, bu yüzden çekmeceyi de kapatmaz. */}
      <IconButton data-nav-toggle={group.id} aria-expanded={expanded}
       aria-label={`${group.label} grubunu ${expanded?'daralt':'genişlet'}`}
       onClick={()=>toggleGroup(group.id)}
       sx={{mt:.75,ml:.25,width:44,height:44,flexShrink:0,borderRadius:2,color:alpha('#e8eef7',.72),'&:hover':{color:'#fff',bgcolor:alpha('#ffffff',.06)}}}>
       {expanded?<ExpandLess sx={{fontSize:18}}/>:<ExpandMore sx={{fontSize:18}}/>}
      </IconButton>
     </Box>
     <Collapse in={expanded} timeout="auto" unmountOnExit>
      <List disablePadding>{group.items.map(item=>renderItem(item,true))}</List>
     </Collapse>
    </Box>;
   })}
  </List>
  <Box sx={{mt:'auto',p:2,color:alpha('#ffffff',.45)}}><Typography variant="caption">Harman Zamanı · v2.9</Typography></Box>
 </Box>;

 return <Box sx={{display:'flex',minHeight:'100dvh'}}>
  <AppBar position="fixed" elevation={0} sx={{ml:{md:`${drawerWidth}px`},width:{md:`calc(100% - ${drawerWidth}px)`},bgcolor:'background.paper',color:'text.primary',borderBottom:'1px solid',borderColor:'divider'}}>
   {/* 390px'te araç çubuğu içeriği 406px sürüyor ve profil avatarı 16px
       kırpılıyordu. xs'te yatay dolgu 12px'ten 4px'e iner; aşağıda firma
       seçicisi ve kardeş boşlukları da daraltılır ki ikon düğmeleri 44px'e
       çıkarıldığında bile içerik ekrana sığsın. sm ve üzeri değişmez. */}
   <Toolbar sx={{minHeight:'72px!important',px:{xs:.5,sm:2.5}}}>
    {/* İkon-yalnız düğmenin erişilebilir adı olmalı; ekran okuyucu aksi halde
        yalnız "button" der. Üretim derlemesinde MUI ikonlarının data-testid'i
        de silindiğinden e2e'nin tutunabileceği kararlı seçici de budur. */}
    {mobile&&<IconButton aria-label="Menüyü aç" onClick={()=>setOpen(true)} edge="start" sx={{mr:{xs:.5,sm:1},minWidth:{xs:44},minHeight:{xs:44}}}><MenuIcon/></IconButton>}
    <Tooltip title="Evrensel arama (Ctrl+K)">
     <Box onClick={()=>setPaletteOpen(true)} sx={{display:{xs:'none',sm:'flex'},alignItems:'center',gap:1,width:{sm:200,lg:300},height:40,px:1.5,border:'1px solid',borderColor:'divider',borderRadius:2,color:'text.secondary',cursor:'pointer','&:hover':{borderColor:'text.disabled',bgcolor:'action.hover'}}}>
      <SearchIcon fontSize="small"/><Typography variant="body2">Ara veya komut çalıştır...</Typography>
      <Box sx={{ml:'auto',fontSize:11,border:'1px solid',borderColor:'divider',borderRadius:1,px:.7,py:.2}}>⌘K</Box>
     </Box>
    </Tooltip>
    {mobile&&<IconButton aria-label="Ara" onClick={()=>setPaletteOpen(true)} sx={{minWidth:{xs:44},minHeight:{xs:44}}}><SearchIcon/></IconButton>}
    <Box sx={{flexGrow:1}}/>
    {/* POS her sayfadan tek tık uzakta: satış gün içindeki en sık iş. */}
    {/* Erişilebilir ad tam hedefi söyler ("Satış" dar ekranda gizlenen görünür
        metindir ve adın içinde geçer); kenar çubuğundaki "Satış" grubuyla
        karışmaz. */}
    {canPos&&<Button variant="contained" aria-label={NAV_LABELS.pos} startIcon={<PointOfSaleIcon/>} onClick={()=>nav('/hizli-satis')}
      sx={{mr:{xs:.5,sm:1},minHeight:44,px:{xs:1.5,sm:2},'& .MuiButton-startIcon':{mr:{xs:0,sm:1}}}}>
      <Box component="span" sx={{display:{xs:'none',sm:'inline'}}}>{NAV_LABELS.posAction}</Box>
     </Button>}
    {/* Aktif firma çok kiracılı kurulumda bakışta okunmalı. Tema+bildirim
        taşma menüsüne indiği için kazanılan 44px buraya verilir: xs'te 84px
        yerine 128px. Kırpma yine olabilir, ama `companyLabels` ortak öneki
        attığı için ekranda kalan kısım ayırt edici olandır. Açılır listede ve
        erişilebilir adda tam ad korunur. */}
    {/* `size="small"` girdiyi ~40px'te bırakıyor; dokunma eşiği için xs'te
        44px'e çekilir. İKİ kural birlikte gerekiyor:
          - OutlinedInput kökü kutunun kendisidir,
          - `.MuiSelect-select` ise `role="combobox"` taşıyan, tıklamayı ALAN
            yüzey. Kök flex olduğu için yalnız köke minHeight vermek iç yüzeyi
            40px'te ortalı bırakıyor, yani gerçek hedef 44'e çıkmıyordu.
        Dikey dolgu simetrik büyütülür; yüzey blok kalır ki adın sonundaki
        ellipsis (…) çalışmaya devam etsin.

        Kural AÇIK medya sorgusuyla sınırlanır. `sx` içinde `{xs:44}` yazmak
        "yalnız mobil" DEĞİL "min-width:0'dan yukarı hepsi" demektir; `sm`
        karşılığı yazılmadıkça masaüstüne de sızar (ölçüldü: 1280px'te kök
        44px, dolgu 10.5px oluyordu). `down('sm')` ile 600px altına kilitlenir,
        768 ve 1280 bugünkü 40px'inde kalır. */}
    {companies.length>0&&<FormControl size="small" sx={theme=>({minWidth:{xs:128,sm:170},maxWidth:{xs:128,sm:'none'},flexShrink:0,mr:{xs:.5,sm:.75},
     [theme.breakpoints.down('sm')]:{
       '& .MuiOutlinedInput-root':{minHeight:44},
       // Sınıf İKİ kez yazılı: MUI aynı sınıfa `min-height:1.4375em` veriyor ve
       // eşit özgüllükte bizim kural kaybediyor (dolgu geçiyor, min-height
       // geçmiyordu; ölçüm 41.13px). Tekrar, özgüllüğü bir basamak yükseltir.
       '& .MuiSelect-select.MuiSelect-select':{minHeight:44,boxSizing:'border-box',paddingTop:'10.5px',paddingBottom:'10.5px'},
       '& .MuiInputBase-root .MuiInputBase-input':{minHeight:44},
       '& .MuiSelect-nativeInput':{minHeight:44,height:44,display:'block'},
       '& .MuiInputBase-input.MuiInputBase-input':{minHeight:44,paddingTop:'10.5px',paddingBottom:'10.5px',boxSizing:'border-box'},
      }})}>
     <InputLabel>Firma</InputLabel>
     <Select label="Firma" value={activeCompany?.id||''} inputProps={{'aria-label':activeCompany?`Aktif firma: ${activeCompany.name}`:'Firma'}}
       renderValue={value=>{const company=companies.find(c=>c.id===Number(value));if(!company)return '';return mobile?(companyLabels.get(company.id)??company.name):company.name}}
       onChange={e=>{const company=companies.find(c=>c.id===Number(e.target.value));if(company)setActiveCompany(company)}}>
      {companies.map(c=><MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
     </Select>
    </FormControl>}
    {/* Bildirim ve tema, dar ekranda TEK bir taşma düğmesine iner: iki 44px
        düğme yerine bir tane yer tutar ve kazanılan 44px firma seçicisine
        gider. Okunmamış bildirim sayısı kaybolmasın diye rozet taşma
        düğmesinin üstünde durur. sm ve üzeri bugünkü iki ayrı düğmede kalır. */}
    {mobile
     ?<>
       <Tooltip title="Bildirimler ve tema"><IconButton aria-label="Bildirimler ve tema" onClick={e=>setMoreAnchor(e.currentTarget)} sx={{minWidth:44,minHeight:44}}><Badge badgeContent={notices.length} color="error"><MoreVertIcon/></Badge></IconButton></Tooltip>
       <Menu anchorEl={moreAnchor} open={Boolean(moreAnchor)} onClose={()=>setMoreAnchor(null)} slotProps={{paper:{sx:{minWidth:280}}}}>
        <MenuItem onClick={()=>{setMoreAnchor(null);toggle()}}>
         <ListItemIcon>{mode==='light'?<DarkModeIcon fontSize="small"/>:<LightModeIcon fontSize="small"/>}</ListItemIcon>
         <ListItemText>{mode==='light'?'Koyu tema':'Açık tema'}</ListItemText>
        </MenuItem>
        {/* Odaklanabilir olmayan başlık: bildirimler listesi altında gelir. */}
        <Box sx={{px:2,pt:1.5,pb:.5,display:'flex',alignItems:'center',gap:1,color:'text.secondary'}}>
         <NotificationsIcon fontSize="small"/><Typography variant="caption" sx={{letterSpacing:'.06em'}}>BİLDİRİMLER</Typography>
        </Box>
        {noticeItems}
       </Menu>
      </>
     :<>
       <Tooltip title="Bildirimler"><IconButton onClick={e=>setNoticeAnchor(e.currentTarget)}><Badge badgeContent={notices.length} color="error"><NotificationsIcon/></Badge></IconButton></Tooltip>
       <Menu anchorEl={noticeAnchor} open={Boolean(noticeAnchor)} onClose={()=>setNoticeAnchor(null)} PaperProps={{sx:{minWidth:320}}}>
        {noticeItems}
       </Menu>
       <Tooltip title={mode==='light'?'Koyu tema':'Açık tema'}><IconButton onClick={toggle}>{mode==='light'?<DarkModeIcon/>:<LightModeIcon/>}</IconButton></Tooltip>
      </>}
    {/* Avatar 34px. Dokunma eşiği için xs'te dolgu 8px'ten 5px'e iner ve düğme
        50px yerine 44px olur; araç çubuğunda 6px yer kazandırır. Kural
        `down('sm')` ile 600px altına kilitlenir: `{xs:44}` yazmak "min-width:0'dan
        yukarı hepsi" demek olduğundan sm karşılığı olmadan masaüstüne de sızıyordu
        (ölçüldü: 1280px'te 44x44, dolgu 5px). sm ve üzeri özgün 50x50'de kalır. */}
    <IconButton aria-label="Profil menüsü" onClick={e=>setAnchor(e.currentTarget)} sx={theme=>({[theme.breakpoints.down('sm')]:{p:.625,minWidth:44,minHeight:44}})}><Avatar sx={{width:34,height:34,bgcolor:'primary.main',fontSize:14,fontWeight:700}}>{user?.display_name?.[0]||'U'}</Avatar></IconButton>
    <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={()=>setAnchor(null)}>
     <MenuItem disabled>{user?.display_name} · {user?.role}</MenuItem>
     <MenuItem onClick={doLogout}>Çıkış Yap</MenuItem>
    </Menu>
   </Toolbar>
  </AppBar>
  {mobile
   ?<Drawer open={open} onClose={()=>setOpen(false)} ModalProps={{keepMounted:true}} sx={{'& .MuiDrawer-paper':{width:drawerWidth}}}>{drawer}</Drawer>
   :<Drawer variant="permanent" sx={{width:drawerWidth,flexShrink:0,'& .MuiDrawer-paper':{width:drawerWidth,border:0}}}>{drawer}</Drawer>}
  <Box component="main" sx={{flexGrow:1,width:{xs:'100%',md:`calc(100% - ${drawerWidth}px)`},pt:{xs:10,md:11},pb:4,px:{xs:1.5,sm:3,lg:4},overflow:'hidden'}}>
   {/* key={pathname}: her rota değişiminde giriş animasyonu yeniden oynar.
       6px bilinçli — 20px "kayıyor" gibi durur ve her gezinmede yorar; 6px
       "yerleşti" hissi verir. Yalnız opacity+transform, yani düzen hesabı yok.
       prefers-reduced-motion tema seviyesinde bunu da susturur. */}
   <Box
    key={location.pathname}
    sx={{
     maxWidth:1560,mx:'auto',
     animation:`routeIn ${sungurTokens.motion.base}ms ${sungurTokens.motion.enter} both`,
     '@keyframes routeIn':{from:{opacity:0,transform:'translateY(6px)'},to:{opacity:1,transform:'none'}},
    }}
   ><Outlet/></Box>
  </Box>
  <CommandPalette open={paletteOpen} onClose={()=>setPaletteOpen(false)}/>
  <Snackbar open={Boolean(logoutError)} onClose={()=>setLogoutError('')}><Alert severity="error">{logoutError}</Alert></Snackbar>
 </Box>;
}
