import {expect,it} from 'vitest';
import {inventoryCountsCsv} from './InventoryCountsReport';

it('rapor CSV metinlerini formül çalıştırmayacak biçimde yazar',()=>{
 const csv=inventoryCountsCsv([{
  id:7,count_date:'2026-07-20',warehouse_name:'\t+Tehlikeli',item_count:1,
  changed_count:1,unchanged_count:0,increase_count:0,decrease_count:1,total_absolute_variance:-3,
 }]);
 expect(csv).toContain('"7";"2026-07-20";"\'\t+Tehlikeli"');
 expect(csv).toContain('"1";"1";"0";"0";"1";"-3"');
});
