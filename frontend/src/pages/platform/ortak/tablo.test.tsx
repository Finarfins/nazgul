/**
 * H71: `Sayfalama` hiçbir sayfa sınamasında doğrudan sınanmıyordu. Etiketler,
 * boyut seçenekleri ve iki geri çağrı burada sabitlenir.
 */
import React from 'react';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';

import {Sayfalama} from './tablo';

afterEach(()=>{cleanup()});

const mount=(sayfa=0)=>{
 const sayfaDegisti=vi.fn();
 const boyutDegisti=vi.fn();
 render(<Sayfalama toplam={120} sayfa={sayfa} boyut={25} sayfaDegisti={sayfaDegisti} boyutDegisti={boyutDegisti}/>);
 return {sayfaDegisti,boyutDegisti};
};

it('Türkçe etiketleri ve aralığı yazar',()=>{
 mount(1);
 expect(screen.getByText('Sayfa başına')).toBeTruthy();
 expect(screen.getByText('26–50 / 120')).toBeTruthy();
});

it('sonraki sayfa düğmesi sıfır tabanlı sayfa numarası bildirir',()=>{
 const {sayfaDegisti}=mount(0);
 fireEvent.click(screen.getByRole('button',{name:/next page/i}));
 expect(sayfaDegisti).toHaveBeenCalledWith(1);
});

it('boyut seçenekleri 25/50/100; seçim sayı olarak bildirilir',()=>{
 const {boyutDegisti}=mount(0);
 fireEvent.mouseDown(screen.getByRole('combobox'));
 const liste=within(screen.getByRole('listbox'));
 expect(liste.getAllByRole('option').map(secenek=>Number(secenek.textContent))).toEqual([25,50,100]);
 fireEvent.click(liste.getByRole('option',{name:'50'}));
 expect(boyutDegisti).toHaveBeenCalledWith(50);
});
