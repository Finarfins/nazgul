import {alpha,createTheme,type PaletteMode,type Shadows} from '@mui/material/styles';
import type {} from '@mui/x-data-grid/themeAugmentation';

export const headerDarkActionVariant = 'darkHeader' as const;

declare module '@mui/material/styles' {
 interface Palette {
  brandTint:string;
  dangerTint:string;
  warningTint:string;
  sidebar:string;
  faint:string;
 }
 interface PaletteOptions {
  brandTint?:string;
  dangerTint?:string;
  warningTint?:string;
  sidebar?:string;
  faint?:string;
 }
}

declare module '@mui/material/Button' {
 interface ButtonPropsVariantOverrides {
  darkHeader: true;
 }
}

export const sungurTokens={
 color:{
  primary:'#164a8a',
  primaryDeep:'#0b3567',
  primaryTint:'#eaf2fb',
  accent:'#f5c400',
  success:'#27845a',
  ink:'#172033',
  muted:'#657186',
  faint:'#95a0b2',
  background:'#f5f7fa',
  surface:'#ffffff',
  line:'#e2e7ef',
  danger:'#c0392b',
  dangerTint:'#fbeceb',
  warning:'#b7791f',
  warningTint:'#fbf1de',
  sidebar:'#071e41',
 },
 radius:{card:14,control:10,compact:8},
 shadow:{card:'0 6px 20px rgba(28,38,32,.06)'},
 focusRing:'0 0 0 3px rgba(22,74,138,.28)',
 // Tek hareket dili. Ekranlarda elle yazılan '.2s'/'.15s' değerleri yerine
 // her yer buradan okur, böylece uygulama tek ritimde hareket eder.
 motion:{
  fast:150,   // renk ve opaklık: anlık hissetmeli
  base:220,   // yüzey, gölge, yükselme
  slow:320,   // panel ve dialog açılışı
  // Giren yavaşlayarak yerleşir, çıkan hızlanarak gider.
  enter:'cubic-bezier(.2,.8,.25,1)',
  exit:'cubic-bezier(.4,0,.7,.2)',
 },
} as const;

// Inter: 13px gövde ve veri yoğun tablolar için çizilmiş bir arayüz yazı tipi.
// Kendi paketimizden servis edilir (CSP script/style-src 'self'), dışarıya
// istek yok. Türkçe ş/ğ/ı/İ latin-ext alt kümesinde; @font-face unicode-range
// taşıdığı için tarayıcı yalnız gereken alt kümeyi indirir.
const font='"Inter Variable", "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

export const getDarkHeaderButtonStyle = (mode:PaletteMode)=>({
 color: mode === 'dark' ? '#f4f7fb' : '#ffffff',
 backgroundColor: alpha('#fff', 0.14),
 border: `1px solid ${alpha('#fff', 0.32)}`,
 '&:hover': {
  backgroundColor: alpha('#fff', 0.22),
  border: `1px solid ${alpha('#fff', 0.42)}`,
 },
});

export const getAppTheme=(mode:PaletteMode)=>{
 const dark=mode==='dark';
 const color=sungurTokens.color;
 const border=dark?'#233b5d':color.line;
 const paper=dark?'#0d213d':color.surface;
 const primaryMain=dark?'#6fa8e8':color.primary;
 const primaryDeep=dark?'#8bbcf0':color.primaryDeep;
 const textPrimary=dark?'#f4f7fb':color.ink;
 const textSecondary=dark?'#aab7ca':color.muted;
 const softShadow=dark?'0 8px 24px rgba(1,9,22,.32)':sungurTokens.shadow.card;
 const shadows=[
  'none',
  softShadow,
  '0 8px 24px rgba(28,38,32,.08)',
  '0 10px 28px rgba(28,38,32,.10)',
  ...Array(21).fill('0 16px 40px rgba(28,38,32,.12)'),
 ] as unknown as Shadows;

 return createTheme({
  palette:{
   mode,
   primary:{main:primaryMain,dark:primaryDeep,light:color.primaryTint,contrastText:'#ffffff'},
   secondary:{main:color.accent,contrastText:'#172033'},
   success:{main:color.success},
   warning:{main:color.warning},
   error:{main:color.danger},
   info:{main:'#3f708f'},
   background:{default:dark?'#071426':color.background,paper},
   divider:border,
   text:{primary:textPrimary,secondary:textSecondary,disabled:dark?'#70839f':color.faint},
   action:{
    hover:dark?alpha('#ffffff',.055):alpha(color.primary,.045),
    selected:dark?alpha(primaryMain,.16):color.primaryTint,
    focus:alpha(color.primary,.18),
   },
   brandTint:dark?alpha(primaryMain,.16):color.primaryTint,
   dangerTint:dark?alpha(color.danger,.2):color.dangerTint,
   warningTint:dark?alpha(color.warning,.2):color.warningTint,
   // Kenar çubuğu rengi yalnız burada tanımlıdır; AppShell `bgcolor:'sidebar'`
   // ile bunu okur (eskiden mavi bir hex değeri gömülüydü ve marka yeşiliyle
   // çelişiyordu). Her iki temada da aynı koyu yeşil kullanılır.
   sidebar:color.sidebar,
   faint:dark?'#70839f':color.faint,
  },
  spacing:8,
  shape:{borderRadius:sungurTokens.radius.control},
  shadows,
  // MUI'nin kendi geçişleri de aynı ritmi kullansın: Dialog, Drawer, Collapse,
  // Tooltip hepsi buradan besleniyor.
  transitions:{
   duration:{
    shortest:sungurTokens.motion.fast,
    shorter:sungurTokens.motion.fast,
    short:sungurTokens.motion.base,
    standard:sungurTokens.motion.base,
    complex:sungurTokens.motion.slow,
   },
   easing:{easeOut:sungurTokens.motion.enter,easeIn:sungurTokens.motion.exit},
  },
  typography:{
   fontFamily:font,
   fontSize:13,
   h1:{fontSize:'1.25rem',fontWeight:700,lineHeight:1.3},
   h2:{fontSize:'1.125rem',fontWeight:700,lineHeight:1.35},
   h3:{fontSize:'1rem',fontWeight:700,lineHeight:1.35},
   h4:{fontSize:'1.25rem',fontWeight:700,lineHeight:1.3},
   h5:{fontSize:'.9375rem',fontWeight:650,lineHeight:1.4},
   h6:{fontSize:'.875rem',fontWeight:650,lineHeight:1.4},
   body1:{fontSize:'.8125rem',lineHeight:1.5},
   body2:{fontSize:'.75rem',lineHeight:1.45},
   caption:{fontSize:'.6875rem',fontWeight:500,lineHeight:1.4},
   button:{fontSize:'.75rem',textTransform:'none',fontWeight:650,letterSpacing:0},
  },
  components:{
   MuiCssBaseline:{
    styleOverrides:{
     html:{fontSize:16},
     body:{fontVariantNumeric:'tabular-nums',WebkitFontSmoothing:'antialiased'},
     '*:focus-visible':{outline:'none',boxShadow:sungurTokens.focusRing},
     // DOKUNMA HEDEFİ KURALLARI BURADAN TAŞINDI — buraya geri koymayın.
     //
     // Eskiden burada `@media (pointer: coarse)` altında `.MuiButton-root`,
     // `.MuiIconButton-root` ve `.MuiOutlinedInput-root` için 44px vardı.
     // Sessizce çalışmıyordu: bileşenlerin kendi `styleOverrides`'ları da
     // min-height bildiriyor, iki kuralın özgüllüğü de aynı (0,1,0) ve
     // MUI'nin ürettiği sınıf kaskadta sonra geliyor. Hangisinin kazandığı
     // emotion'ın o kombinasyon için ürettiği sınıfa göre DEĞİŞİYORDU:
     // ölçümde `size="small"` butonlar 34px, başlıktaki contained butonlar
     // 40px kalırken başka butonlar 44px'e çıkıyordu.
     //
     // Kural artık her bileşenin kendi `styleOverrides`'ında, aynı bildirimin
     // içinde; kaskad sırası önemsiz, sonuç tahmin edilebilir.
     // Hareket hassasiyeti olan kullanıcıda TÜM animasyon susar. Tek nokta:
     // sonradan eklemek 79 ekranı tek tek gezmek demek olurdu.
     '@media (prefers-reduced-motion: reduce)':{
      '*,*::before,*::after':{
       animationDuration:'.01ms!important',
       animationIterationCount:'1!important',
       transitionDuration:'.01ms!important',
       scrollBehavior:'auto!important',
      },
     },
     '@media print':{
      body:{background:'#fff!important'},
      '.MuiDrawer-root,.MuiAppBar-root':{display:'none!important'},
      'main.MuiBox-root':{width:'100%!important',margin:'0!important',padding:'0!important'},
      '.MuiPaper-root':{boxShadow:'none!important'},
     },
    },
   },
   MuiButton:{
    defaultProps:{disableElevation:true},
    styleOverrides:{
     root:{
      minHeight:40,borderRadius:sungurTokens.radius.compact,paddingInline:16,
      // Dokunmatikte asgari hedef 44px (Apple 44, Material 48 önerir).
      '@media (pointer: coarse)':{minHeight:44},
      transition:`background-color ${sungurTokens.motion.fast}ms ${sungurTokens.motion.enter},`
       +`border-color ${sungurTokens.motion.fast}ms ${sungurTokens.motion.enter},`
       +`transform ${sungurTokens.motion.fast}ms ${sungurTokens.motion.enter}`,
      // Basıldığını parmak hissetsin; dokunmatikte tıklamanın kaydedildiğini
      // gösteren en ucuz geri bildirim.
      '&:active':{transform:'scale(.98)'},
     },
     // 34px MASAÜSTÜ ölçüsüdür. Dokunmatikte CssBaseline'daki
     // `@media (pointer: coarse)` kuralı 44px'e çıkarmayı amaçlıyordu ama
     // ÇALIŞMIYORDU: iki kuralın da özgüllüğü aynı (0,1,0) ve MUI'nin bileşen
     // sınıfı kaskadta sonra geldiği için `sizeSmall` kazanıyor, buton 34px
     // kalıyordu. Ölçüldü: orta buton/simge butonu/metin girişi 44px'e
     // çıkıyordu, YALNIZ small butonlar dışarıda kalıyordu — yani niyet
     // koddaydı ama tam da en küçük hedeflerde etkisizdi.
     // Kuralı buraya taşımak sorunu kaynağında çözer; artık aynı bildirimin
     // içinde olduğu için kaskad sırası önemsiz.
     sizeSmall:{minHeight:34,paddingInline:12,'@media (pointer: coarse)':{minHeight:44}},
     sizeLarge:{minHeight:52,paddingInline:22},
     containedPrimary:{'&:hover':{backgroundColor:color.primaryDeep}},
     outlined:{backgroundColor:paper,borderColor:border,'&:hover':{borderColor:primaryMain,backgroundColor:alpha(primaryMain,.04)}},
    },
    variants:[
     {props:{variant:headerDarkActionVariant},style:getDarkHeaderButtonStyle(mode)},
    ],
   },
   MuiIconButton:{styleOverrides:{root:{
    borderRadius:sungurTokens.radius.compact,
    '@media (pointer: coarse)':{minWidth:44,minHeight:44},
   }}},
   MuiTextField:{defaultProps:{size:'small'}},
   MuiFormControl:{defaultProps:{size:'small'}},
   MuiOutlinedInput:{
    styleOverrides:{
     root:{
      minHeight:40,
      '@media (pointer: coarse)':{minHeight:44},
      borderRadius:sungurTokens.radius.compact,
      backgroundColor:paper,
      '& .MuiOutlinedInput-notchedOutline':{borderColor:border},
      '&:hover .MuiOutlinedInput-notchedOutline':{borderColor:primaryMain},
      '&.Mui-focused':{
       boxShadow:sungurTokens.focusRing,
       '& .MuiOutlinedInput-notchedOutline':{borderColor:primaryMain,borderWidth:1},
      },
     },
     input:{padding:'10px 12px'},
    },
   },
   MuiInputLabel:{styleOverrides:{root:{fontWeight:600}}},
   MuiMenu:{styleOverrides:{paper:{marginTop:6,border:`1px solid ${border}`,boxShadow:'0 12px 28px rgba(28,38,32,.14)'}}},
   MuiMenuItem:{styleOverrides:{root:{minHeight:40,borderRadius:6,margin:'2px 6px'}}},
   MuiPaper:{styleOverrides:{root:{backgroundImage:'none'}}},
   MuiCard:{styleOverrides:{root:{
    border:`1px solid ${border}`,
    boxShadow:softShadow,
    borderRadius:sungurTokens.radius.card,
    transition:`box-shadow ${sungurTokens.motion.base}ms ${sungurTokens.motion.enter},`
     +`transform ${sungurTokens.motion.base}ms ${sungurTokens.motion.enter},`
     +`border-color ${sungurTokens.motion.base}ms ${sungurTokens.motion.enter}`,
    // Bilinçli olarak YALNIZ tıklanabilir kart tepki verir. Veri yoğun bir
    // ERP'de her yüzeyi oynatmak gürültü olur; hareket bir anlam taşımalı.
    '&[role="button"]:hover,&[tabindex="0"]:hover':{
     // transform + box-shadow: ikisi de düzeni yeniden hesaplatmaz, sadece
     // boyar. 700 satırlık listede fark hissedilir.
     transform:'translateY(-2px)',
     boxShadow:shadows[3],
     borderColor:alpha(primaryMain,.35),
    },
    '&[role="button"]:active,&[tabindex="0"]:active':{transform:'translateY(0)',transition:'none'},
   }}},
   MuiCardContent:{styleOverrides:{root:{padding:18,'&:last-child':{paddingBottom:18}}}},
   MuiDialog:{styleOverrides:{paper:{border:`1px solid ${border}`,borderRadius:sungurTokens.radius.card,boxShadow:'0 24px 70px rgba(28,38,32,.22)'}}},
   MuiDialogTitle:{styleOverrides:{root:{fontSize:'1rem',fontWeight:650,padding:'18px 24px 14px'}}},
   MuiDialogContent:{styleOverrides:{root:{padding:'8px 24px 24px'}}},
   MuiDialogActions:{styleOverrides:{root:{padding:'16px 24px'}}},
   MuiChip:{styleOverrides:{root:{borderRadius:sungurTokens.radius.compact,fontWeight:650},sizeSmall:{height:24}}},
   MuiAlert:{styleOverrides:{
    root:{borderRadius:sungurTokens.radius.control},
    standardSuccess:dark?{
     color:textPrimary,
     backgroundColor:'#0d213d',
     border:'1px solid #2f745f',
     '& .MuiAlert-icon':{color:'#55c493'},
    }:undefined,
   }},
   MuiTooltip:{defaultProps:{arrow:true}},
   MuiTableRow:{styleOverrides:{root:{'&:hover':{backgroundColor:alpha(primaryMain,.035)}}}},
   MuiTableCell:{styleOverrides:{root:{borderColor:border},alignRight:{fontVariantNumeric:'tabular-nums'}}},
   MuiDataGrid:{
    styleOverrides:{
     root:{
      border:0,
      fontSize:12.5,
      fontVariantNumeric:'tabular-nums',
      '& .MuiDataGrid-columnHeaders':{backgroundColor:dark?'#091a31':color.background,borderBottom:`1px solid ${border}`},
      '& .MuiDataGrid-columnHeaderTitle':{fontWeight:650,color:textSecondary},
      '& .MuiDataGrid-row':{
       transition:`background-color ${sungurTokens.motion.fast}ms ${sungurTokens.motion.enter}`,
       '&:hover':{backgroundColor:alpha(primaryMain,.04)},
      },
      '& .MuiDataGrid-cell':{borderColor:border},
      '& .MuiDataGrid-footerContainer':{borderColor:border},
     },
    },
   },
  },
 });
};

export const theme=getAppTheme('light');
