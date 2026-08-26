import {describe,expect,it} from 'vitest';
import {documentGrossDecimal,documentVatDecimal,manualTotalDiscount,partLineTotalDecimal} from './documentMoney';

describe('iş emri parça satır toplamı (backend money.compute_line paritesi)',()=>{
 // İskonto + KDV içeren satırda backend her bileşeni AYRI kuruşa yuvarlar:
 //   raw=money(1*money(10.10))=10.10
 //   discount=money(10.10*33/100)=money(3.333)=3.33
 //   taxable=money(10.10-3.33)=6.77
 //   tax=money(6.77*15/100)=money(1.0155)=1.02
 //   total=money(6.77+1.02)=7.79
 // Naif float (q*u*(1-d/100)*(1+t/100)) ise 10.10*0.67*1.15=7.78205 → 7.78.
 // Paylaşılan yardımcı backend'le birebir 7.79 vermeli, float 7.78 DEĞİL.
 it('iskonto ve KDV içeren satırı compute_line ile birebir hesaplar',()=>{
  const total=partLineTotalDecimal({quantity:'1',unit_price:'10.10',discount:'33',tax_rate:'15'});
  expect(total.toFixed(2)).toBe('7.79');
  const naiveFloat=Number((1*10.10*(1-33/100)*(1+15/100)).toFixed(2));
  expect(naiveFloat).toBe(7.78);
  expect(total.toFixed(2)).not.toBe(naiveFloat.toFixed(2));
 });

 it('miktarı 4 haneye, birim fiyatı 2 haneye normalize eder', ()=>{
  // unit_price 3. hane yukarı yuvarlanır (money): 2.005 → 2.01, ×3 = 6.03.
  expect(partLineTotalDecimal({quantity:'3',unit_price:'2.005',discount:'0',tax_rate:'0'}).toFixed(2)).toBe('6.03');
 });
});

describe('elle genel toplam Decimal hesabı',()=>{
 it('kuruşlu farkı sabit belge iskontosuna çevirir',()=>{
  const gross=documentGrossDecimal([{quantity:'1',unit_price:'32261.71',discount_percent:'0'}]);
  const result=manualTotalDiscount(gross,'32000');
  expect(gross.toFixed(2)).toBe('32261.71');
  expect(result.discount.toFixed(2)).toBe('261.71');
  expect(result.target.toFixed(2)).toBe('32000.00');
  expect(result.percent.toFixed(4)).toBe('0.8112');
  expect(documentVatDecimal([
   {quantity:'1',unit_price:'32261.71',discount_percent:'0',vat_rate:'20'},
  ],result.target).toFixed(2)).toBe('5333.33');
 });

 it('satır toplamından büyük hedefi negatif iskonto olarak kabul etmez',()=>{
  expect(()=>manualTotalDiscount('100.00','100.01')).toThrow(/büyük olamaz/);
 });
});
