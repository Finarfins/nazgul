import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';

import OnScreenKeyboard from './OnScreenKeyboard';

describe('OnScreenKeyboard',()=>{afterEach(()=>{cleanup();vi.unstubAllGlobals()});
 it('sayısal değeri ilk tuşta değiştirir, ondalık ve düzenleme tuşlarını uygular',()=>{
  const onChange=vi.fn();
  const {rerender}=render(<OnScreenKeyboard mode="numeric" value="18" activeLabel="Birim fiyat" replaceOnNextKey onChange={onChange} onClose={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Tuş 2'}));
  expect(onChange).toHaveBeenLastCalledWith('2');
  rerender(<OnScreenKeyboard mode="numeric" value="2" activeLabel="Birim fiyat" onChange={onChange} onClose={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Tuş ,'}));
  expect(onChange).toHaveBeenLastCalledWith('2,');
  rerender(<OnScreenKeyboard mode="numeric" value="2," activeLabel="Birim fiyat" onChange={onChange} onClose={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Tuş 5'}));
  expect(onChange).toHaveBeenLastCalledWith('2,5');
  fireEvent.click(screen.getByRole('button',{name:'Sil'}));
  expect(onChange).toHaveBeenLastCalledWith('2');
  fireEvent.click(screen.getByRole('button',{name:'Temizle'}));
  expect(onChange).toHaveBeenLastCalledWith('');
 });

 it('Türkçe küçük ve büyük karakter, boşluk ve enter üretir',()=>{
  const onChange=vi.fn();
  const onEnter=vi.fn();
  const onClose=vi.fn();
  const {rerender}=render(<OnScreenKeyboard mode="text" value="" activeLabel="Ürün arama" onChange={onChange} onEnter={onEnter} onClose={onClose}/>);
  fireEvent.click(screen.getByRole('button',{name:'Tuş ş'}));
  expect(onChange).toHaveBeenLastCalledWith('ş');
  rerender(<OnScreenKeyboard mode="text" value="ş" activeLabel="Ürün arama" onChange={onChange} onEnter={onEnter} onClose={onClose}/>);
  fireEvent.click(screen.getByRole('button',{name:'Büyük küçük harf'}));
  fireEvent.click(screen.getByRole('button',{name:'Tuş Ü'}));
  expect(onChange).toHaveBeenLastCalledWith('şÜ');
  rerender(<OnScreenKeyboard mode="text" value="şÜ" activeLabel="Ürün arama" onChange={onChange} onEnter={onEnter} onClose={onClose}/>);
  fireEvent.click(screen.getByRole('button',{name:'Boşluk'}));
  expect(onChange).toHaveBeenLastCalledWith('şÜ ');
  fireEvent.click(screen.getByRole('button',{name:'Enter'}));
  expect(onEnter).toHaveBeenCalledOnce();
  expect(onClose).toHaveBeenCalledOnce();
 });

 it('metin modunda sayı satırı üretir ve Caps bunu bozmaz',()=>{
  const onChange=vi.fn();
  const {rerender}=render(<OnScreenKeyboard mode="text" value="hffh - " activeLabel="Ürün / parça arama" onChange={onChange} onClose={vi.fn()}/>);
  for(const digit of ['1','2','3','4','5','6','7','8','9','0'])screen.getByRole('button',{name:`Tuş ${digit}`});
  fireEvent.click(screen.getByRole('button',{name:'Tuş 2'}));
  expect(onChange).toHaveBeenLastCalledWith('hffh - 2');
  rerender(<OnScreenKeyboard mode="text" value="hffh - 2" activeLabel="Ürün / parça arama" onChange={onChange} onClose={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Büyük küçük harf'}));
  fireEvent.click(screen.getByRole('button',{name:'Tuş 3'}));
  expect(onChange).toHaveBeenLastCalledWith('hffh - 23');
 });

 it('sayısal modda rakam tuşu aktif alana yazar',()=>{
  const onChange=vi.fn();
  render(<OnScreenKeyboard mode="numeric" value="1" activeLabel="Miktar" onChange={onChange} onClose={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Tuş 5'}));
  expect(onChange).toHaveBeenLastCalledWith('15');
 });

 it('320px ekranda Caps, Sil ve Enter hedeflerini en az 44px tutar',()=>{
  vi.stubGlobal('innerWidth',320);
  window.dispatchEvent(new Event('resize'));
  render(<OnScreenKeyboard mode="text" value="" activeLabel="Not" onChange={vi.fn()} onClose={vi.fn()}/>);

  for(const name of ['Büyük küçük harf','Sil','Enter']){
   const style=getComputedStyle(screen.getByRole('button',{name}));
   expect(parseFloat(style.minWidth)).toBeGreaterThanOrEqual(44);
   expect(parseFloat(style.minHeight)).toBeGreaterThanOrEqual(44);
  }
 });
});
