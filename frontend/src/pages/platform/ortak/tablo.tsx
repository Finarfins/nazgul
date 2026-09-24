/** Platform listelerinin sunucu tarafı sayfalaması. */
import {TablePagination} from '@mui/material';

const SAYFA_BOYUTLARI=[25,50,100];

/** Sunucu tarafı sayfalama (limit/offset) — ActivityLog.tsx ile aynı düzen. */
export function Sayfalama({toplam,sayfa,boyut,sayfaDegisti,boyutDegisti}:{
 toplam:number;sayfa:number;boyut:number;sayfaDegisti:(sayfa:number)=>void;boyutDegisti:(boyut:number)=>void;
}){
 return <TablePagination sx={{
   '& .MuiIconButton-root':{minWidth:{xs:44,md:38},minHeight:{xs:44,md:38}},
   '& .MuiTablePagination-select':{minHeight:{xs:44,md:32},display:'flex',alignItems:'center'},
  }}
  component="div" count={toplam} page={sayfa} rowsPerPage={boyut} rowsPerPageOptions={SAYFA_BOYUTLARI}
  labelRowsPerPage="Sayfa başına" labelDisplayedRows={({from,to,count})=>`${from}–${to} / ${count}`}
  onPageChange={(_,yeni)=>sayfaDegisti(yeni)}
  onRowsPerPageChange={olay=>boyutDegisti(Number(olay.target.value))}/>;
}
