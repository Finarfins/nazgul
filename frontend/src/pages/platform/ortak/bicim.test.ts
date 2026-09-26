/**
 * H71: platform biçimleyicileri. `yasMetni` sayfa sınamalarında yalnız iki
 * değerle (7260 sn, 900 sn) dolaylı geçiyordu, `tarihSaat` hiç sınanmıyordu;
 * burada eşikler tek tek sınanır.
 */
import {afterEach,describe,expect,it} from 'vitest';

import {tarihSaat,yasMetni} from './bicim';

describe('yasMetni',()=>{
 it('boş değer tire yazar',()=>{
  expect(yasMetni(null)).toBe('—');
  expect(yasMetni(undefined)).toBe('—');
 });
 it('dakikanın altı saniye, yuvarlanır',()=>{
  expect(yasMetni(0)).toBe('0 sn');
  expect(yasMetni(44.6)).toBe('45 sn');
  expect(yasMetni(59)).toBe('59 sn');
 });
 it('saatin altı tam dakika, aşağı yuvarlanır',()=>{
  expect(yasMetni(60)).toBe('1 dk');
  expect(yasMetni(3599)).toBe('59 dk');
 });
 it('günün altı saat ve (varsa) dakika',()=>{
  expect(yasMetni(3600)).toBe('1 sa');
  expect(yasMetni(7260)).toBe('2 sa 1 dk');
  expect(yasMetni(86399)).toBe('23 sa 59 dk');
 });
 it('gün ve üstü tam gün',()=>{
  expect(yasMetni(86400)).toBe('1 gün');
  expect(yasMetni(3*86400-1)).toBe('2 gün');
 });
});

describe('tarihSaat',()=>{
 it('boş değer tire yazar',()=>{
  expect(tarihSaat(null)).toBe('—');
  expect(tarihSaat(undefined)).toBe('—');
  expect(tarihSaat('')).toBe('—');
 });
 // H86: eskiden yalnız /^15\.06\.2026 …$/ deseni sınanıyordu ve sonuç makinenin
 // saat dilimine bağlıydı; TZ=Etc/GMT-12 (UTC+12) altında "16.06.2026
 // 00:00:00" verip kırmızıya dönüyordu. Artık süreç TZ'si değiştirilerek
 // (Node her atamada dilim önbelleğini sıfırlar) tam dize sınanır.
 const surecTz=process.env.TZ;
 afterEach(()=>{
  if(surecTz===undefined)delete process.env.TZ;else process.env.TZ=surecTz;
 });
 it.each(['UTC','Etc/GMT-12','Etc/GMT+12'])('makine TZ=%s olsa da İstanbul duvar saatini yazar',tz=>{
  process.env.TZ=tz;
  expect(tarihSaat('2026-06-15T12:00:00Z')).toBe('15.06.2026 15:00:00');
  // UTC'de gün dönmeden İstanbul'da dönen an: gün de İstanbul'a göre.
  expect(tarihSaat('2026-06-15T22:30:00Z')).toBe('16.06.2026 01:30:00');
 });
});
