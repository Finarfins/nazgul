import {useEffect} from 'react';
import type {Dispatch,SetStateAction} from 'react';
import type Decimal from 'decimal.js';

// Kalem tablolarında ortak davranış: son satırın zorunlu alanları (ürün + miktar)
// dolduğunda altına boş bir satır açılır, böylece kullanıcı "Kalem ekle"ye basmaz.
// Boş satır kaydedilmez; belge kaydı zaten product_id olmayan satırları eler.
export type AutoLine={product_id:number|null;quantity:Decimal.Value};

export const isLineFilled=(line:AutoLine|undefined):boolean=>Boolean(line&&line.product_id)&&Number(line?.quantity)>0;
export const isLineBlank=(line:AutoLine|undefined):boolean=>!line?.product_id;

export function useAutoTrailingLine<T extends AutoLine>(
 active:boolean,
 lines:T[],
 setLines:Dispatch<SetStateAction<T[]>>,
 makeBlankLine:()=>T,
){
 useEffect(()=>{
  if(!active)return;
  if(!isLineFilled(lines[lines.length-1]))return;
  setLines(current=>isLineFilled(current[current.length-1])?[...current,makeBlankLine()]:current);
 },[active,lines,setLines,makeBlankLine]);
}
