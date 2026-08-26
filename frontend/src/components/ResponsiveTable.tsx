import React from 'react';
import type { ReactNode } from 'react';
import {Box,Button,Card,CardActionArea,CardContent,CircularProgress,Stack,Typography,useMediaQuery,useTheme} from '@mui/material';
import InboxOutlinedIcon from '@mui/icons-material/InboxOutlined';
import type {Breakpoint} from '@mui/material/styles';
import {DataGrid,type GridColDef} from '@mui/x-data-grid';
type CardField={label:string;value:(row:any)=>ReactNode};
type CardAction={label:string;ariaLabel?:(row:any)=>string;icon?:ReactNode;color?:'primary'|'secondary'|'error'|'warning'|'info'|'success'|'inherit';disabled?:(row:any)=>boolean;hidden?:(row:any)=>boolean;onClick:(row:any)=>void};
type Props={rows:any[];columns:GridColDef[];loading?:boolean;cardTitle:(row:any)=>string;cardSubtitle?:(row:any)=>string;cardFields:CardField[];cardActions?:CardAction[];cardActionsOnDesktop?:boolean;cardBreakpoint?:Breakpoint;onRowClick?:(row:any)=>void;getRowId?:(row:any)=>string|number;desktopRowHeight?:number;hideFooter?:boolean};
const EmptyOverlay=()=> <Stack height="100%" minHeight={210} alignItems="center" justifyContent="center" spacing={1} textAlign="center" px={2}><Box sx={{width:52,height:52,borderRadius:3,display:'grid',placeItems:'center',bgcolor:'brandTint',color:'primary.main'}}><InboxOutlinedIcon/></Box><Typography fontWeight={800}>Henüz kayıt yok</Typography><Typography variant="body2" color="text.secondary">Yeni bir kayıt oluşturduğunuzda burada görünecek.</Typography></Stack>;
const LoadingOverlay=()=> <Stack height="100%" minHeight={210} alignItems="center" justifyContent="center" spacing={1.5}><CircularProgress size={30}/><Typography variant="body2" color="text.secondary">Kayıtlar yükleniyor…</Typography></Stack>;
/**
 * Kart görünümüne geçiş eşiği VARSAYILANI 'sm' → 'md' (900px) yapıldı.
 *
 * ÖLÇÜM (gerçek veriyle, /musteriler tablosu, 9 sütun ~1260px):
 *
 *   768px  portre tablet    tablo hiç görünmüyor, kart olmalı
 *   1024px yatay tablet     9 sütunun 3'ü gizli, 553px yatay kaydırma
 *   1200px dar masaüstü     9 sütunun 3'ü gizli, 393px yatay kaydırma
 *
 * Buradan çıkan sonuç ilk sandığımdan farklı: bu tablo YALNIZ tablette değil,
 * ~1600px'in altındaki HER genişlikte taşıyor. DataGrid'in kendi yatay
 * kaydırması bu uygulamanın geniş tablolar için baştan beri kullandığı desen.
 *
 * Bu yüzden eşiği 'lg'ye çekmek "tablo sığsın" sonucunu VERMEZ — yalnız kart
 * eşiğini masaüstüne taşır. 'md' seçildi: 768px'te kart açık kazanç (orada
 * tablo kullanılamaz durumda), 1024px'te ise davranış dar bir masaüstü
 * penceresiyle AYNI kalıyor. Yani bu değişiklik portre tableti düzeltiyor,
 * yatay tableti olduğu gibi bırakıyor.
 *
 * Precedent: Transactions.tsx eşiği zaten 'lg' yapmış (tablosu daha da geniş).
 * Tablosu gerçekten dar olan bir ekran `cardBreakpoint="sm"` geçerek eski
 * davranışa dönebilir.
 */
export default function ResponsiveTable({rows,columns,loading,cardTitle,cardSubtitle,cardFields,cardActions,cardActionsOnDesktop=false,cardBreakpoint='md',onRowClick,getRowId,desktopRowHeight=54,hideFooter=false}:Props){const theme=useTheme();const cards=useMediaQuery(theme.breakpoints.down(cardBreakpoint));const lastRowClick=React.useRef(0);
const actionButtons=(row:any,mobile:boolean)=>cardActions?.filter(action=>!action.hidden?.(row)).map(action=><Button key={action.label} size="small" variant="outlined" color={action.color||'primary'} startIcon={action.icon} disabled={action.disabled?.(row)??false} aria-label={action.ariaLabel?.(row)??`${cardTitle(row)} — ${action.label}`} onClick={event=>{event.stopPropagation();action.onClick(row)}} sx={mobile?{minHeight:44}:undefined}>{action.label}</Button>);
// `cardActions` has historically been mobile-only. Opt-in avoids duplicating
// actions on older callers whose supplied DataGrid columns already contain an
// actions column, while converted tables can explicitly preserve desktop
// functionality through the same action contract.
const desktopColumns:GridColDef[]=!cardActionsOnDesktop||!cardActions?.length?columns:[...columns,{
  field:'__responsive_actions',headerName:'İşlemler',sortable:false,filterable:false,
  width:Math.min(420,Math.max(140,cardActions.length*95)),
  renderCell:params=><Stack direction="row" spacing={.5} alignItems="center" height="100%">{actionButtons(params.row,false)}</Stack>,
 }];
if(!cards)return <Box sx={{height:rows.length?'calc(100dvh - 300px)':300,minHeight:rows.length?470:300,bgcolor:'background.paper',borderRadius:3,overflow:'hidden',border:'1px solid',borderColor:'divider'}}><DataGrid rows={rows} columns={desktopColumns} getRowId={getRowId} loading={loading} slots={{noRowsOverlay:EmptyOverlay,loadingOverlay:LoadingOverlay}} disableRowSelectionOnClick hideFooter={hideFooter} pageSizeOptions={[25,50,100]} rowHeight={desktopRowHeight} columnHeaderHeight={46} initialState={{pagination:{paginationModel:{pageSize:25,page:0}}}} onRowClick={p=>{
 // Çift tıkın İKİNCİ tıkını yut. Windows'ta çift tıklamak yaygın alışkanlık;
 // iki tık iki kez gezinirse geçmişe aynı adres iki kez girer ve kullanıcı
 // "geri"ye bastığında aynı sayfada kalır — geri tuşu bozuk görünür.
 const now=Date.now();
 if(now-lastRowClick.current<600)return;
 lastRowClick.current=now;
 onRowClick?.(p.row);
}} sx={{'& .MuiDataGrid-columnHeaders':{bgcolor:'action.hover',borderBottom:'1px solid',borderColor:'divider'},'& .MuiDataGrid-cell':{display:'flex',alignItems:'center',borderBottom:'1px solid',borderColor:'divider'},'& .MuiDataGrid-row':{cursor:onRowClick?'pointer':'default','&:hover':{bgcolor:'rgba(22,74,138,.045)'}}}}/></Box>;
if(loading)return <Card variant="outlined"><LoadingOverlay/></Card>;
if(!rows.length)return <Card variant="outlined"><EmptyOverlay/></Card>;
// Satır açılabilir DEĞİLKEN kart gövdesi düz `CardContent` olarak çizilir.
// Eskiden her durumda `CardActionArea` sarıyordu ve `onRowClick` yoksa
// `disabled` kalıyordu; MUI devre dışı ButtonBase'e `pointer-events:none`
// verdiği için kart alanlarının İÇİNDEKİ bağlantılar tıklanamaz oluyordu
// (mobil görünüm masaüstünün veri/etkileşim alt kümesine düşüyordu). Satır
// açılabilir olduğunda davranış aynen korunur.
// UZUN VE BOŞLUKSUZ METİN KIRILIR — KIRPILMAZ.
//
// Kart `overflow:'hidden'` taşıyor (aşağıda), bu yüzden karta sığmayan içerik
// kaydırılamaz: ekranda HİÇ görünmez ve kaydırma çubuğu da çıkmaz. Boşluklu bir
// ad kendiliğinden alt satıra geçtiği için sorun çıkarmıyordu; boşluksuz TEK bir
// uzun sözcük (kullanıcının bitişik yazdığı unvan, uzun kod, e-posta benzeri ad)
// hiçbir yerden kırılamadığı için kartı taşırıyordu. 390px'te ölçüldü:
// +61px KIRPILMIŞ — yani müşteri adının sonu kullanıcıya hiç ulaşmıyordu.
//
// `overflowWrap:'anywhere'` sözcüğü gerektiğinde ortasından kırar. `minWidth:0`
// esnek satırlar için ŞART: flex çocuğunun öntanımlı `min-width:auto` değeri onu
// içeriğinden daha dar olmaya bırakmaz, dolayısıyla kırma kuralı tek başına
// yetmez ve satır yine taşardı.
const BREAK_LONG_WORDS={overflowWrap:'anywhere'} as const;
const body=(row:any)=><CardContent><Typography fontWeight={750} sx={BREAK_LONG_WORDS}>{cardTitle(row)}</Typography>{cardSubtitle&&<Typography variant="body2" color="text.secondary" mb={1.5} sx={BREAK_LONG_WORDS}>{cardSubtitle(row)}</Typography>}<Stack spacing={.8}>{cardFields.map((f,i)=><Box key={`${f.label}-${i}`} display="flex" justifyContent="space-between" gap={2}><Typography variant="body2" color="text.secondary" sx={{minWidth:0,...BREAK_LONG_WORDS}}>{f.label}</Typography><Typography component="div" variant="body2" fontWeight={650} textAlign="right" sx={{minWidth:0,...BREAK_LONG_WORDS}}>{f.value(row)}</Typography></Box>)}</Stack></CardContent>;
return <Stack spacing={1.2}>{rows.map(row=><Card key={getRowId?getRowId(row):row.id} data-responsive-row="" sx={{overflow:'hidden'}}>{onRowClick?<CardActionArea onClick={()=>onRowClick(row)}>{body(row)}</CardActionArea>:body(row)}
{/* Actions sit outside CardActionArea: nesting buttons is invalid markup and would
    break keyboard focus, so this also guarantees they never open the row. */}
{/* `size="small"` düğmeleri 34px yüksekliğinde bırakıyordu; kart görünümü
    dokunmatik ekranın birincil düzeni olduğu için xs'te 44px'e çıkarılır.
    sm ve üzerinde MUI'nin `size="small"` ölçüsü aynen korunur. */}
{cardActions?.length?<Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap px={2} pb={1.75}>{actionButtons(row,true)}</Stack>:null}
</Card>)}</Stack>}
