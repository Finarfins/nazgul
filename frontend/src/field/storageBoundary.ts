import ts from 'typescript';

export type StorageSource={file:string;source:string};
const API_ALLOW:Record<string,ReadonlySet<string>>={
 // Tarla kuyruğu (mobil-erp#2, FAZ 4) BİLİNÇLİ olarak AYRI bir veritabanı
 // açıyor ve bu yüzden listeye ekleniyor.
 //
 // Sahanın deposuna (`fieldDatabase.ts`) taşımak DAHA KÖTÜ olurdu: o modül
 // iş emri önbelleği, snapshot ve ek dosyalarıyla birlikte tek bir şema
 // sürümü yönetiyor. Tarla kuyruğunu oraya koymak, tarla tarafındaki her şema
 // değişikliğinde SAHA veritabanının sürümünü artırmak demekti — ve saha
 // sürüm yükseltmesi teknisyenin cihazındaki gönderilmemiş iş emirlerini
 // riske atar. İki alanın çevrimdışı verisi ayrı yaşam döngüsüne sahip.
 indexedDB:new Set(['field/fieldDatabase.ts','field/storageBoundary.ts','field/store.test.ts',
  'farm/farmOutbox.ts']),
 // Tarla sezon/politika önbelleğinin tek tarayıcı depolama adaptörü. Sayfa ve
 // testler ham localStorage yerine bu modülün güvenli API'sini kullanır.
 localStorage:new Set(['AuthContext.tsx','ThemeContext.tsx','api.ts','field/fieldDatabase.ts','field/store.test.ts',
  'farm/farmMetadataCache.ts']),
 sessionStorage:new Set(['staleChunk.ts','staleChunk.test.ts','field/fieldDatabase.ts','field/store.test.ts']),
 caches:new Set(['field/fieldDatabase.ts','field/store.test.ts']),
};
const DATABASE_IMPORT_ALLOW=new Set([
 'AuthContext.tsx','field/sessionCoordinator.ts','field/snapshotSource.ts','field/store.ts',
 'field/store.test.ts','field/snapshotSource.test.ts','field/coordinator.ts','vite-env.d.ts',
 // Saha yazma kuyruğu. Depoya doğrudan erişmesi ZORUNLU: kuyruk kaydını
 // silmek, çakışma yazmak ve önbelleği tazelemek TEK bir IndexedDB
 // işleminde olmalı. Bunları store.ts üzerinden geçirmek işlemi bölerdi ve
 // arada kesilen bir gönderim kuyruğu boş, önbelleği eski bırakabilirdi.
 'field/fieldOutbox.ts','field/fieldOutbox.test.ts',
]);
const ANALYZER_ALLOW=new Set(['field/storageBoundary.ts','field/architecture.test.ts']);
const databaseBoundaryProtected=(file:string)=>!DATABASE_IMPORT_ALLOW.has(file)&&!ANALYZER_ALLOW.has(file);
const staticText=(node:ts.Expression):string|null=>{
 if(ts.isStringLiteralLike(node))return node.text;
 if(ts.isBinaryExpression(node)&&node.operatorToken.kind===ts.SyntaxKind.PlusToken){
  const left=staticText(node.left),right=staticText(node.right);return left===null||right===null?null:left+right;
 }
 return null;
};
const storageRoot=(node:ts.Expression):string=>{
 if(ts.isIdentifier(node))return API_ALLOW[node.text]?node.text:'';
 if(ts.isPropertyAccessExpression(node)){
  if(API_ALLOW[node.name.text])return node.name.text;
  return storageRoot(node.expression);
 }
 if(ts.isElementAccessExpression(node)&&node.argumentExpression){
  const member=staticText(node.argumentExpression);
  if(member&&API_ALLOW[member])return member;
  return storageRoot(node.expression);
 }
 return'';
};
const literal=(node:ts.Expression)=>ts.isStringLiteralLike(node)?node.text:null;
const containsConnectionReference=(node:ts.Node):boolean=>{
 let found=false;
 const visit=(child:ts.Node)=>{if(ts.isIdentifier(child)&&child.text==='connection')found=true;else ts.forEachChild(child,visit)};
 visit(node);return found;
};

export function storageBoundaryViolations(sources:StorageSource[]){
 const violations:string[]=[];
 for(const {file,source} of sources){
  const ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,file.endsWith('x')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
  const report=(kind:string,node:ts.Node)=>violations.push(`${kind}:${file}:${ast.getLineAndCharacterOfPosition(node.getStart()).line+1}`);
  const visit=(node:ts.Node)=>{
   if(ts.isCallExpression(node)){
    const expression=node.expression;
    if(expression.kind===ts.SyntaxKind.ImportKeyword){
     const module=node.arguments[0]?staticText(node.arguments[0]):null;
     if(databaseBoundaryProtected(file)&&module===null)report('unresolved-dynamic-import',node);
     else if(module?.includes('fieldDatabase')&&!DATABASE_IMPORT_ALLOW.has(file))report('dynamic-database-import',node);
    }
    const owner=ts.isPropertyAccessExpression(expression)||ts.isElementAccessExpression(expression)
      ?storageRoot(expression.expression):'';
    const member=ts.isPropertyAccessExpression(expression)?expression.name.text
      :ts.isElementAccessExpression(expression)&&expression.argumentExpression?staticText(expression.argumentExpression):null;
    const guarded=owner==='indexedDB'&&Boolean(member)&&['open','databases','deleteDatabase'].includes(member!)
      ||(owner==='localStorage'||owner==='sessionStorage')&&Boolean(member)
      ||owner==='caches'&&member==='open';
    if(guarded&&!API_ALLOW[owner].has(file))report(`${owner}.${member}`,node);
    if(ts.isPropertyAccessExpression(expression)&&expression.name.text==='open'
      &&ts.isElementAccessExpression(expression.expression)
      &&expression.expression.argumentExpression
      &&staticText(expression.expression.argumentExpression)===null
      &&!API_ALLOW.indexedDB.has(file)&&!ANALYZER_ALLOW.has(file))report('unresolved-computed-open',node);
   }
   if(ts.isStringLiteralLike(node)&&node.text.includes('field/fieldDatabase')
     &&databaseBoundaryProtected(file))report('database-path-literal',node);
   if(ts.isIdentifier(node)&&node.text==='indexedDB'&&!API_ALLOW.indexedDB.has(file))report('indexedDB-token',node);
   if(ts.isIdentifier(node)&&node.text==='connection'
     &&!new Set(['field/fieldDatabase.ts','field/storageBoundary.ts']).has(file))report('database-connection-token',node);
   if(ts.isElementAccessExpression(node)&&node.argumentExpression&&staticText(node.argumentExpression)==='indexedDB'
     &&!API_ALLOW.indexedDB.has(file))report('indexedDB-bracket',node);
   if(ts.isVariableStatement(node)&&node.modifiers?.some(modifier=>modifier.kind===ts.SyntaxKind.ExportKeyword)
     &&containsConnectionReference(node))report('database-connection-export',node);
   if(ts.isImportDeclaration(node)&&literal(node.moduleSpecifier)?.includes('fieldDatabase')
     &&!DATABASE_IMPORT_ALLOW.has(file))report('database-import',node);
   if(ts.isExportDeclaration(node)&&node.moduleSpecifier&&literal(node.moduleSpecifier)?.includes('fieldDatabase')
     &&file!=='field/store.ts')report('database-re-export',node);
   if(ts.isExportDeclaration(node)&&!node.moduleSpecifier&&node.exportClause&&ts.isNamedExports(node.exportClause)
     &&node.exportClause.elements.some(element=>(element.propertyName||element.name).text==='connection')){
    report('database-connection-export',node);
   }
   ts.forEachChild(node,visit);
  };
  visit(ast);
 }
 return violations;
}
