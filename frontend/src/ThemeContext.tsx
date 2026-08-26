import React from 'react';
import {createContext,useContext,useMemo,useState,type ReactNode} from 'react';import {ThemeProvider,CssBaseline} from '@mui/material';
import {getAppTheme} from './theme';
type Mode='light'|'dark';const C=createContext<{mode:Mode;toggle:()=>void}|null>(null);
export function AppThemeProvider({children}:{children:ReactNode}){const [mode,setMode]=useState<Mode>(()=>(localStorage.getItem('yhp_theme') as Mode)||'light');const toggle=()=>setMode(m=>{const n=m==='light'?'dark':'light';localStorage.setItem('yhp_theme',n);return n});const theme=useMemo(()=>getAppTheme(mode),[mode]);return <C.Provider value={{mode,toggle}}><ThemeProvider theme={theme}><CssBaseline/>{children}</ThemeProvider></C.Provider>};export function useAppTheme(){const v=useContext(C);if(!v)throw new Error('theme');return v;}
