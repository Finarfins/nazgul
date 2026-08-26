import React from 'react';
import {Component, type ErrorInfo, type ReactNode} from 'react';
import {Alert,Box,Button,CircularProgress,Stack,Typography} from '@mui/material';
import {isStaleChunkError,reloadOnceForStaleChunk} from '../staleChunk';

type Props={children:ReactNode};
type State={error:Error|null;reloading:boolean};

export default class ErrorBoundary extends Component<Props,State>{
  state:State={error:null,reloading:false};
  static getDerivedStateFromError(error:Error):Partial<State>{return {error};}
  componentDidCatch(error:Error,info:ErrorInfo){
    console.error('UI error',error,info);
    // Dağıtım sonrası bayat chunk: bir kez otomatik yenile, kullanıcıya çökme
    // ekranı gösterme. Koruma penceresi içinde ikinci kez olursa normal hata
    // ekranına düşer.
    if(isStaleChunkError(error)&&reloadOnceForStaleChunk())this.setState({reloading:true});
  }
  render(){
    if(this.state.reloading)return <Box minHeight="100dvh" display="grid" sx={{placeItems:'center'}}><Stack spacing={2} alignItems="center"><CircularProgress/><Typography color="text.secondary">Uygulama güncellendi, sayfa yenileniyor...</Typography></Stack></Box>;
    if(!this.state.error)return this.props.children;
    return <Box minHeight="100dvh" display="grid" sx={{placeItems:'center',p:3}}>
      <Stack spacing={2} maxWidth={560} width="100%">
        <Typography variant="h5" fontWeight={900}>Sayfa yüklenemedi</Typography>
        <Alert severity="error">{this.state.error.message||'Beklenmeyen bir arayüz hatası oluştu.'}</Alert>
        <Button variant="contained" onClick={()=>location.reload()}>Sayfayı Yenile</Button>
      </Stack>
    </Box>;
  }
}
