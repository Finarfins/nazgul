import { defineConfig } from 'vitest/config';
import {cpus} from 'node:os';
import react from '@vitejs/plugin-react';
import {createHash} from 'node:crypto';
import {cp,mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {join,relative,sep} from 'node:path';

const sha256=(bytes:Buffer)=>createHash('sha256').update(bytes).digest('hex');
async function filesUnder(root:string,dir=root):Promise<string[]>{
 const entries=await readdir(dir,{withFileTypes:true});
 return (await Promise.all(entries.map(entry=>entry.isDirectory()?filesUnder(root,join(dir,entry.name)):[join(dir,entry.name)]))).flat();
}
function fieldPrecachePlugin(){
 return {name:'field-precache-integrity',apply:'build' as const,async closeBundle(){
  const dist=join(process.cwd(),'dist');
  await mkdir(join(dist,'saha'),{recursive:true});
  await cp(join(dist,'index.html'),join(dist,'saha','index.html'),{recursive:true});
  const files=(await filesUnder(dist)).filter(file=>!file.endsWith(join('field-pwa','sw-v1.js'))&&
   (file.endsWith('.html')||file.endsWith('.js')||file.endsWith('.css')||file.endsWith('.webmanifest')||file.endsWith('.png')));
  const assets=await Promise.all(files.map(async file=>{const rel=relative(dist,file).split(sep).join('/');return{url:rel==='saha/index.html'?'/saha/':'/'+rel,sha256:sha256(await readFile(file))}}));
  const manifestHash=sha256(Buffer.from(JSON.stringify(assets)));
  const workerPath=join(dist,'field-pwa','sw-v1.js');
  const worker=(await readFile(workerPath,'utf8')).replace('__FIELD_RELEASE_MANIFEST__',JSON.stringify({hash:manifestHash,assets}));
  await writeFile(workerPath,worker,'utf8');
 }};
}

export default defineConfig({
  plugins: [react(),fieldPrecachePlugin()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:5050' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    // Playwright spec'leri (e2e/) vitest'in eline geçmesin: onlar gerçek
    // tarayıcı/gerçek backend ister ve `playwright test` ile koşar.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],

    // İŞÇİ SAYISI ÇEKİRDEĞİN YARISI — ÖLÇÜLDÜ, seçilmedi.
    //
    // vitest varsayılanı çekirdek sayısı kadar işçi açar. Sekiz çekirdekli bir
    // makinede sekiz işçi CPU'yu doyuruyor ve `waitFor`un 1000 ms'lik bütçesi
    // dolmadan React render'ı bitmiyor; sonuç bir zaman aşımı değil, `waitFor`un
    // son hatayı yeniden fırlatmasıyla gelen bir ASSERTION oluyor:
    //   AssertionError: expected [ <input …(7)></input> ] to have a length of 2 but got 1
    //
    // 8 çekirdekli bir kutuda ölçülen doz-yanıt (aynı ağaç, aynı süit, 78 dosya):
    //   --no-file-parallelism  532/532 geçti   251.88s
    //   --maxWorkers=1         532/532 geçti
    //   --maxWorkers=2         532/532 geçti   135.47s
    //   --maxWorkers=4         532/532 geçti    83.40s
    //   --maxWorkers=8         528/532, 4 düştü 75.44s
    // Düşen üç test tek başına koşunca GEÇİYOR (11/11), yani kusur dosyada değil.
    //
    // NEDEN ZAMAN AŞIMINI BÜYÜTMEK DEĞİL: `asyncUtilTimeout`u yükseltmek dört
    // testi kurtarmak için 532 testin tamamının beklemeye razı olduğu süreyi
    // uzatır ve gerçek bir performans gerilemesine saklanacak yer açar. İşçi
    // sayısını sınırlamak NASIL KOŞTUĞUMUZU değiştirir, NE İDDİA ETTİĞİMİZİ değil.
    //
    // SINIR, DÜRÜSTÇE: çekirdek/2 bir PAYI HEURİSTİĞİDİR, kanıt değil. Daha yavaş
    // bir makinede yarıya inmiş sayı da pencereyi kaçırabilir. Bilinen şey şu:
    // bu süit paya ihtiyaç duyuyor ve sekiz-çekirdekte-sekiz-işçi onu vermiyor.
    // Sabit bir tamsayı yazılmadı: o, yazılmamış bir donanım varsayımı olurdu.
    maxWorkers: Math.max(1, Math.floor(cpus().length / 2)),
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    target: 'es2020',
    chunkSizeWarningLimit: 1200,
  },
});
