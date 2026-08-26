import {readdirSync,readFileSync,statSync} from 'node:fs';
import {join,relative,resolve} from 'node:path';
import {describe,expect,it} from 'vitest';

import {storageBoundaryViolations,type StorageSource} from './storageBoundary';

const root=resolve(process.cwd(),'src');
function collect(dir=root,result:StorageSource[]=[]):StorageSource[]{
 for(const entry of readdirSync(dir)){const absolute=join(dir,entry);
  if(statSync(absolute).isDirectory())collect(absolute,result);
  else if(/\.(ts|tsx)$/.test(entry))result.push({file:relative(root,absolute).replaceAll('\\','/'),source:readFileSync(absolute,'utf8')});
 }
 return result;
}
const violation=(file:string,source:string)=>storageBoundaryViolations([...collect(),{file,source}])
 .filter(item=>item.includes(`:${file}:`)).map(item=>item.split(':').slice(0,2).join(':'));

describe('structural offline storage boundary',()=>{
 it('AST-scans the complete frontend source tree against the minimal allow-list',()=>{
  expect(storageBoundaryViolations(collect())).toEqual([]);
 });
 it('catches a dynamic import escape',()=>{
  expect(violation('escape/dynamic.ts',"await import('../field/fieldDatabase')")).toContain('dynamic-database-import:escape/dynamic.ts');
 });
 it('catches a computed dynamic import escape',()=>{
  expect(violation('escape/computed-import.ts',"await import('../field/' + 'fieldDatabase')"))
   .toContain('dynamic-database-import:escape/computed-import.ts');
 });
 it('fails closed when a dynamic import argument cannot be resolved statically',()=>{
  expect(violation('escape/variable-import.ts',"const modulePath = getModulePath(); await import(modulePath)"))
   .toContain('unresolved-dynamic-import:escape/variable-import.ts');
 });
 it('catches a field database path hidden in a variable before dynamic import',()=>{
  const source="const modulePath = '../field/fieldDatabase'; const dbModule = await import(modulePath)";
  expect(violation('escape/literal-variable-import.ts',source))
   .toContain('database-path-literal:escape/literal-variable-import.ts');
  expect(violation('escape/literal-variable-import.ts',source))
   .toContain('unresolved-dynamic-import:escape/literal-variable-import.ts');
 });
 it('catches a concatenated database name',()=>{
  expect(violation('escape/concat.ts',"indexedDB.open('sungur-' + 'field-m1')")).toContain('indexedDB.open:escape/concat.ts');
 });
 it('catches bracket access',()=>{
  expect(violation('escape/bracket.ts',"window['indexedDB'].open(name)")).toContain('indexedDB-bracket:escape/bracket.ts');
 });
 it('catches computed globalThis IndexedDB access',()=>{
  expect(violation('escape/computed.ts',"globalThis['indexed'+'DB'].open(name)")).toContain('indexedDB.open:escape/computed.ts');
 });
 it('catches an IndexedDB variable alias',()=>{
  expect(violation('escape/idb-alias.ts',"const db = indexedDB; db.open('sungur-field-m1')"))
   .toContain('indexedDB-token:escape/idb-alias.ts');
 });
 it('catches a non-static globalThis storage expression',()=>{
  expect(violation('escape/template.ts',"const prefix='indexed'; globalThis[`${prefix}DB`].open(name)"))
   .toContain('unresolved-computed-open:escape/template.ts');
 });
 it('fails closed for computed open through an aliased root',()=>{
  expect(violation('escape/root-alias.ts',"const root = globalThis; root[key].open('sungur-field-m1')"))
   .toContain('unresolved-computed-open:escape/root-alias.ts');
 });
 it('catches an aliased import',()=>{
  expect(violation('escape/alias.ts',"import {applySnapshot as write} from '../field/fieldDatabase'")).toContain('database-import:escape/alias.ts');
 });
 it('catches an indirect re-export',()=>{
  expect(violation('escape/reexport.ts',"export {applySnapshot} from '../field/fieldDatabase'")).toContain('database-re-export:escape/reexport.ts');
 });
 it('catches an exported raw database connection getter',()=>{
  expect(violation('escape/getDb.ts',"const connection={}; export const getDb = () => connection"))
   .toContain('database-connection-export:escape/getDb.ts');
 });
 it('catches a local raw connection export',()=>{
  expect(violation('escape/local-export.ts',"const connection={}; export {connection}"))
   .toContain('database-connection-export:escape/local-export.ts');
 });
 it('catches a transitive raw connection alias export',()=>{
  expect(violation('escape/transitive.ts',"const connection={}; const db=connection; export const getDb=()=>db"))
   .toContain('database-connection-token:escape/transitive.ts');
 });
});
