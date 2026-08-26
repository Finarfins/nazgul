import {expect,it} from 'vitest';
import {inventoryCountsCsv} from './InventoryCounts';

it('CSV metinlerinde formül enjeksiyonunu engeller ve sayıları korur',()=>{
 const csv=inventoryCountsCsv([{
  id:9,count_date:'2026-07-20',warehouse_name:'  =Tehlikeli',item_count:1,
  changed_count:1,unchanged_count:0,increase_count:0,decrease_count:1,total_absolute_variance:-2,
 }]);
 expect(csv.startsWith('\uFEFF')).toBe(true);
 expect(csv).toContain('"9";"2026-07-20";"\'  =Tehlikeli"');
 expect(csv).toContain('"1";"1";"0";"0";"1";"-2"');
});
