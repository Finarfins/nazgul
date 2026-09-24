/**
 * H71: platform biçimleyicileri. `yasMetni` sayfa sınamalarında yalnız iki
 * değerle (7260 sn, 900 sn) dolaylı geçiyordu, `tarihSaat` hiç sınanmıyordu;
 * burada eşikler tek tek sınanır.
 */
import {describe,expect,it} from 'vitest';

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
 it('ISO anı tr-TR gün.ay.yıl saat:dk:sn biçiminde yazar',()=>{
  expect(tarihSaat('2026-06-15T12:00:00Z')).toMatch(/^15\.06\.2026 \d{2}:\d{2}:\d{2}$/);
 });
});
