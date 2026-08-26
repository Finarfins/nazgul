import Decimal from 'decimal.js';

Decimal.set({precision:40,rounding:Decimal.ROUND_HALF_UP});

export const decimal=(value:Decimal.Value|string|null|undefined)=>{
 const normalized=String(value??0).trim().replace(',','.');
 try{return new Decimal(normalized||0)}catch{return new Decimal(0)}
};

export const moneyDecimal=(value:Decimal.Value)=>decimal(value).toDecimalPlaces(2);
export const percentageDecimal=(value:Decimal.Value)=>decimal(value).toDecimalPlaces(4);
// Backend money.quantity() karşılığı: miktar 4 haneye normalize edilir.
export const quantityDecimal=(value:Decimal.Value)=>decimal(value).toDecimalPlaces(4);

export type PartLineAmount={quantity:Decimal.Value;unit_price:Decimal.Value;discount:Decimal.Value;tax_rate:Decimal.Value};
// İş emri parça satır toplamı — backend money.compute_line ile BİREBİR aynı:
// her bileşen ayrı ayrı kuruşa yuvarlanır (miktar 4h, birim fiyat 2h, oranlar
// 4h). Sıra: raw → iskonto → vergilenebilir → KDV → toplam. Float aritmetiği
// yok; frontend gösterimi stored total_price ve fatura satırıyla tutar.
export const partLineTotalDecimal=(line:PartLineAmount)=>{
 const raw=moneyDecimal(quantityDecimal(line.quantity).mul(moneyDecimal(line.unit_price)));
 const discount=moneyDecimal(raw.mul(percentageDecimal(line.discount)).div(100));
 const taxable=moneyDecimal(raw.minus(discount));
 const tax=moneyDecimal(taxable.mul(percentageDecimal(line.tax_rate)).div(100));
 return moneyDecimal(taxable.plus(tax));
};

export type MoneyLine={
 quantity:Decimal.Value;
 unit_price:Decimal.Value;
 discount_percent:Decimal.Value;
 vat_rate?:Decimal.Value;
};

export const lineGrossDecimal=(line:MoneyLine)=>{
 const raw=moneyDecimal(decimal(line.quantity).mul(moneyDecimal(line.unit_price)));
 const discount=moneyDecimal(raw.mul(percentageDecimal(line.discount_percent)).div(100));
 const total=moneyDecimal(raw.minus(discount));
 return total.isNegative()?moneyDecimal(0):total;
};

export const documentGrossDecimal=(lines:MoneyLine[])=>
 moneyDecimal(lines.reduce((sum,line)=>sum.plus(lineGrossDecimal(line)),new Decimal(0)));

export const documentVatDecimal=(lines:MoneyLine[],finalValue:Decimal.Value)=>{
 const gross=documentGrossDecimal(lines);
 const subtotal=moneyDecimal(lines.reduce((sum,line)=>{
  const lineGross=lineGrossDecimal(line);
  const rate=percentageDecimal(line.vat_rate??0);
  return sum.plus(rate.isZero()?lineGross:moneyDecimal(lineGross.div(rate.div(100).plus(1))));
 },new Decimal(0)));
 const finalTotal=moneyDecimal(finalValue);
 if(gross.isZero())return moneyDecimal(0);
 const discountedSubtotal=moneyDecimal(subtotal.mul(finalTotal).div(gross));
 return moneyDecimal(finalTotal.minus(discountedSubtotal));
};

export const percentageDiscountDecimal=(gross:Decimal.Value,percent:Decimal.Value)=>
 moneyDecimal(decimal(gross).mul(percentageDecimal(percent)).div(100));

export const manualTotalDiscount=(grossValue:Decimal.Value,targetValue:string)=>{
 const gross=moneyDecimal(grossValue);
 const target=moneyDecimal(decimal(targetValue));
 if(target.isNegative())throw new Error('Genel toplam negatif olamaz.');
 if(target.greaterThan(gross))throw new Error('Genel toplam satır toplamından büyük olamaz; fiyat farkı için iskonto kullanılamaz.');
 const discount=moneyDecimal(gross.minus(target));
 const percent=gross.isZero()?new Decimal(0):percentageDecimal(discount.mul(100).div(gross));
 return {target,discount,percent};
};

export const decimalPayload=(value:Decimal.Value)=>decimal(value).toFixed();
