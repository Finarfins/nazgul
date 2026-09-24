import {describe,expect,it} from 'vitest';
import {yerelGun} from './tarih';

const yerel=(iso:string)=>{
 const an=new Date(iso);
 return `${an.getFullYear()}-${String(an.getMonth()+1).padStart(2,'0')}-${String(an.getDate()).padStart(2,'0')}`;
};

describe('yerelGun (H73)',()=>{
 it('UTC ISO damgasini new Date ile ayristirip YEREL gunu yazar',()=>{
  const iso='2026-09-24T21:22:20.054785+00:00';
  expect(Number.isNaN(new Date(iso).getTime())).toBe(false);
  expect(yerelGun(iso)).toBe(yerel(iso));
  expect(yerelGun(iso)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
 });
 it('ayni ani gosteren eski bicimler AYNI gunu verir',()=>{
  // H73 oncesi PG `+03:00`, SQLite bosluklu yaziyordu; hepsi ayni an.
  const beklenen=yerelGun('2026-09-24T21:22:20.054785+00:00');
  expect(yerelGun('2026-09-25T00:22:20.054785+03:00')).toBe(beklenen);
 });
 it('yalniz tarih oldugu gibi kalir, bos deger tire olur',()=>{
  expect(yerelGun('2026-07-31')).toBe('2026-07-31');
  expect(yerelGun(null)).toBe('-');
  expect(yerelGun(undefined)).toBe('-');
  expect(yerelGun('')).toBe('-');
 });
 it('ayristirilamayan deger eski slice davranisina duser',()=>{
  expect(yerelGun('bozuk-deger-xyz')).toBe('bozuk-dege');
 });
});
