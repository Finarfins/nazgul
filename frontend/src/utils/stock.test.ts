import {describe,expect,it} from 'vitest';
import {movementTarget,stockCondition} from './stock';

describe('stock helpers',()=>{
 it('fiziksel sayım hareketini gerçek sayım belgesine bağlar',()=>{
  expect(movementTarget('inventory_count',41)).toEqual({path:'/stok-sayimlari/41',openId:41});
 });

 it('geçersiz veya referanssız hareketi belgeye bağlamaz',()=>{
  expect(movementTarget('inventory_count',null)).toBeNull();
  expect(movementTarget('unknown',41)).toBeNull();
 });

 it('negatif, sıfır, kritik ve normal stoğu ayrı sınıflandırır',()=>{
  expect(stockCondition(-1,5).key).toBe('negative');
  expect(stockCondition(0,5).key).toBe('out');
  expect(stockCondition(3,5).key).toBe('critical');
  expect(stockCondition(8,5).key).toBe('normal');
 });
});
