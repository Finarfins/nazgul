import React from 'react';
import {createContext,useContext,useEffect,useMemo,useState,type ReactNode} from 'react';
import {api} from './api';
import {abortFieldSyncs,broadcastFieldLogout} from './field/sessionCoordinator';
import {clearAllFieldData,lockFieldWrites,rememberOnlineIdentity,unlockFieldWritesAfterLogin} from './field/fieldDatabase';
import {readFieldAccess} from './field/store';

type User={id:number;username:string;display_name:string;role:string;must_change_password:boolean};
type Company={id:number;name:string;tax_number?:string|null;is_default?:boolean};
type AuthValue={user:User|null;permissions:string[];isPlatformOperator:boolean;companies:Company[];activeCompany:Company|null;setActiveCompany:(company:Company)=>void;loading:boolean;login:(username:string,password:string)=>Promise<boolean>;changePassword:(currentPassword:string,newPassword:string)=>Promise<void>;logout:()=>Promise<void>;can:(permission:string)=>boolean};
const AuthContext=createContext<AuthValue|null>(null);

export function AuthProvider({children}:{children:ReactNode}){
 const [user,setUser]=useState<User|null>(null);
 const [permissions,setPermissions]=useState<string[]>([]);
 const [isPlatformOperator,setIsPlatformOperator]=useState(false);
 const [companies,setCompanies]=useState<Company[]>([]);
 const [activeCompany,setActiveCompanyState]=useState<Company|null>(()=>{try{return JSON.parse(localStorage.getItem('yhp_company')||'null')}catch{return null}});
 const [loading,setLoading]=useState(true);
 const apply=(data:any)=>{
  unlockFieldWritesAfterLogin();
  setUser(data.user);
  setPermissions(data.permissions||[]);
  setIsPlatformOperator(Boolean(data.is_platform_operator));
  setCompanies(data.companies||[]);
  const selected=(data.companies||[]).find((item:Company)=>item.id===activeCompany?.id)||(data.companies||[])[0]||null;
  setActiveCompanyState(selected);
  if(selected)localStorage.setItem('yhp_company',JSON.stringify(selected));
  if(selected&&data.user&&(data.permissions||[]).includes('field_service')){
   void rememberOnlineIdentity({companyId:selected.id,companyName:selected.name,userId:data.user.id,
    user:data.user,permissions:data.permissions||[]});
  }
 };
 useEffect(()=>{
  // Remove legacy localStorage credentials once. Authentication is now solely
  // based on HttpOnly cookies, so JavaScript cannot read the access token.
  localStorage.removeItem('yhp_token');
  localStorage.removeItem('yhp_user');
  const expired=()=>{setUser(null);setPermissions([]);setIsPlatformOperator(false);setCompanies([])};
  window.addEventListener('yhp:auth-expired',expired);
  api.get('/auth/me').then(({data})=>apply(data)).catch(async()=>{
   const access=await readFieldAccess().catch(()=>null);const cached=access?.meta;
   if(cached){setUser(cached.user);setPermissions(cached.permissions);setCompanies([{id:cached.companyId,name:cached.companyName}]);setActiveCompanyState({id:cached.companyId,name:cached.companyName})}
   else expired();
  }).finally(()=>setLoading(false));
  return()=>window.removeEventListener('yhp:auth-expired',expired);
 },[]);
 const login=async(username:string,password:string)=>{const {data}=await api.post('/auth/login',{username,password});apply(data);return Boolean(data.user?.must_change_password)};
 const changePassword=async(currentPassword:string,newPassword:string)=>{await api.post('/auth/change-password',{current_password:currentPassword,new_password:newPassword});setUser(current=>current?{...current,must_change_password:false}:current)};
 const logout=async()=>{try{await api.post('/auth/logout')}catch{/* offline logout still closes local write surface */}
  await lockFieldWrites();abortFieldSyncs();
  try{await clearAllFieldData()}catch(error){throw new Error('Cihaz verileri temizlenemedi; cihaz kilitli. Çıkışı tekrar deneyin.',{cause:error})}
  broadcastFieldLogout();
  localStorage.removeItem('yhp_token');localStorage.removeItem('yhp_user');localStorage.removeItem('yhp_company');setUser(null);setPermissions([]);setIsPlatformOperator(false);setCompanies([]);setActiveCompanyState(null)};
 const setActiveCompany=(company:Company)=>{setActiveCompanyState(company);localStorage.setItem('yhp_company',JSON.stringify(company));window.location.reload()};
 const can=(permission:string)=>permission==='platform'?isPlatformOperator:permissions.includes('*')||permissions.includes(permission);
 const value=useMemo(()=>({user,permissions,isPlatformOperator,companies,activeCompany,setActiveCompany,loading,login,changePassword,logout,can}),[user,permissions,isPlatformOperator,companies,activeCompany,loading]);
 return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error('AuthProvider eksik');return value;}
