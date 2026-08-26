import React from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,DialogTitle,Stack,TextField} from '@mui/material';

type Props={
 open:boolean;
 detail:string;
 reason:string;
 busy:boolean;
 error?:string;
 onReasonChange:(value:string)=>void;
 onClose:()=>void;
 onConfirm:()=>void;
};

export default function PolicyOverrideDialog({open,detail,reason,busy,error,onReasonChange,onClose,onConfirm}:Props){
 return <Dialog open={open} onClose={busy?undefined:onClose} fullWidth maxWidth="sm">
  <DialogTitle>Yönetici politika onayı</DialogTitle>
  <DialogContent>
   <Stack spacing={2} mt={1}>
    <Alert severity="warning">{detail}</Alert>
    {error&&<Alert severity="error">{error}</Alert>}
    <TextField
     autoFocus
     label="Onay gerekçesi"
     value={reason}
     onChange={event=>onReasonChange(event.target.value)}
     helperText="En az 5 karakter. Kullanıcı, belge ve istek bilgileriyle denetim kaydı oluşturulur."
     multiline
     minRows={3}
     inputProps={{maxLength:500}}
     required
    />
   </Stack>
  </DialogContent>
  <DialogActions>
   <Button onClick={onClose} disabled={busy}>Vazgeç</Button>
   <Button variant="contained" color="warning" onClick={onConfirm} disabled={busy||reason.trim().length<5}>
    {busy?'Onay uygulanıyor...':'Onayla ve devam et'}
   </Button>
  </DialogActions>
 </Dialog>
}
