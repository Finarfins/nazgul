import {describe,expect,it} from 'vitest';
import {ALL_NAV_ITEMS,permissionForPath} from '../navigation';

const visibleItems=(can:(permission:string)=>boolean)=>
 ALL_NAV_ITEMS.filter(item=>can(permissionForPath(item.path)));

describe('platform backup navigation',()=>{
 it('hides Yedekler from an admin who is not on the operator allow-list',()=>{
  const adminCan=(permission:string)=>permission!=='platform';
  expect(visibleItems(adminCan).some(item=>item.path==='/yedekler')).toBe(false);
 });

 it('shows Yedekler to an allow-listed platform operator',()=>{
  expect(visibleItems(()=>true).some(item=>item.path==='/yedekler')).toBe(true);
 });
});
