// APP.TSX ROTA AYRIŞTIRICISI — kaynağın YAPISINDAN, metninden değil.
//
// ÖLÇÜLEN BOŞLUK. Bu iş daha önce tek bir düzenli ifadeyle yapılıyordu:
//
//     /<Route\s+path="([^"]+)"/g
//
// Bu desen `path` ÖZNİTELİĞİNİN İLK SIRADA ve ÇİFT TIRNAKLI olmasına
// bağlıydı. İki gerçek JSX biçimi onu sessizce atlıyordu ve atlanan rota
// envanterde de aranmadığı için G1 "iki küme eşit" deyip YEŞİL kalıyordu:
//
//     <Route element={<X/>} path="yeni-rota"/>      // sıra ters
//     <Route path='yeni-rota' element={<X/>}/>      // tek tırnak
//
// Eski parite hesabı da (`rota + element= + 1 == <Route sayısı`) bu kaçışı
// göremiyordu: atlanan rota `element=` taşıdığı için düzen rotası sayılıp
// toplamı denk getiriyordu. Yani kapının kendi doğrulaması da aynı kör noktayı
// paylaşıyordu.
//
// ÇÖZÜM: TypeScript'İN KENDİ AYRIŞTIRICISI. `typescript` zaten bu projenin bir
// devDependency'si (tsc -b, typescript-eslint); yeni ve ağır bir bağımlılık
// eklenmez. AST'de öznitelik SIRASI ve tırnak BİÇİMİ diye bir kavram yoktur:
// `path` bir `JsxAttribute`tir, değeri bir `StringLiteral`dır ve TypeScript
// onun metnini tırnaktan bağımsız verir.
//
// FAIL-CLOSED. Ayrıştırıcı ANLAMADIĞI hiçbir `<Route>` biçimini sessizce
// geçmez; `path={degisken}`, `{...props}` ya da çözülemeyen bir şablon
// gördüğünde İSTİSNA fırlatır. Kapının yeşili "hiçbir şey bulamadım"dan
// gelemez.

import {readFileSync} from 'node:fs';

import ts from 'typescript';

/** Bir `<Route>` etiketinin dört tasnifinden biri. */
export type RouteTuru =
  /** `path="..."` taşıyan, ekran çizen rota. */
  | 'adlandirilmis'
  /** `index` — üst rotanın kendi yolu. */
  | 'index'
  /** `path="*"` — rota DEĞİL, yakalayıcı (`<Navigate to="/">`). */
  | 'yakalayici'
  /** Ne `path` ne `index` taşıyan düzen rotası (ör. `<Route element={<Protected/>}>`). */
  | 'duzen';

export interface AyristirilmisRoute {
  readonly tur: RouteTuru;
  /** `adlandirilmis` ve `index` için `/` ile başlayan TAM yol; diğerlerinde `null`. */
  readonly rota: string | null;
  /** Kaynaktaki satır numarası (1'den başlar) — ihlal iletileri adres versin. */
  readonly satir: number;
}

export interface AyristirmaSonucu {
  readonly hepsi: readonly AyristirilmisRoute[];
  /** `adlandirilmis` + `index` yolları, kaynaktaki sırayla (yinelenenler KORUNUR). */
  readonly rotalar: readonly string[];
  readonly indexSayisi: number;
  readonly yakalayiciSayisi: number;
  readonly duzenSayisi: number;
}

class RotaAyristirmaHatasi extends Error {}

function hata(dosya: string, dugum: ts.Node, kaynak: ts.SourceFile, ileti: string): never {
  const {line} = kaynak.getLineAndCharacterOfPosition(dugum.getStart(kaynak));
  throw new RotaAyristirmaHatasi(
    `${dosya}:${line + 1} — ROTA AYRIŞTIRILAMADI: ${ileti}. ` +
      'Ayrıştırıcı anlamadığı bir <Route> biçimini SESSİZCE GEÇMEZ; ' +
      'App.tsx yeni bir biçim kullanıyorsa e2e/rota-ayristirici.ts ONA göre genişletilmeli.',
  );
}

/** JSX etiket adı — `<Route>` ve `<Router.Route>` gibi biçimler için düz metin. */
function etiketAdi(etiket: ts.JsxTagNameExpression): string {
  return etiket.getText();
}

/** Bir `JsxAttribute` değerinden düz metin. Tırnak biçimi AST'de YOKTUR. */
function ozniteliktenMetin(
  dosya: string,
  kaynak: ts.SourceFile,
  oznitelik: ts.JsxAttribute,
): string {
  const deger = oznitelik.initializer;
  if (deger === undefined) {
    hata(dosya, oznitelik, kaynak, `\`${oznitelik.name.getText()}\` özniteliğinin değeri yok`);
  }
  if (ts.isStringLiteral(deger)) return deger.text;
  if (ts.isJsxExpression(deger)) {
    const ifade = deger.expression;
    if (ifade && (ts.isStringLiteral(ifade) || ts.isNoSubstitutionTemplateLiteral(ifade))) {
      return ifade.text;
    }
    hata(
      dosya,
      oznitelik,
      kaynak,
      `\`${oznitelik.name.getText()}\` sabit bir metin değil (ifade: ${deger.getText()})`,
    );
  }
  hata(dosya, oznitelik, kaynak, `\`${oznitelik.name.getText()}\` beklenmedik bir değer taşıyor`);
}

/** `index` özniteliği: çıplak `index` ya da `index={true}`. */
function indexMi(dosya: string, kaynak: ts.SourceFile, oznitelik: ts.JsxAttribute): boolean {
  const deger = oznitelik.initializer;
  if (deger === undefined) return true;
  if (ts.isJsxExpression(deger) && deger.expression) {
    if (deger.expression.kind === ts.SyntaxKind.TrueKeyword) return true;
    if (deger.expression.kind === ts.SyntaxKind.FalseKeyword) return false;
  }
  hata(dosya, oznitelik, kaynak, '`index` sabit bir doğruluk değeri değil');
}

/** Üst yol ile alt yolu React Router'ın yaptığı gibi birleştirir. */
function yolBirlestir(ust: string, alt: string): string {
  if (alt.startsWith('/')) return alt;
  const govde = ust === '/' ? '' : ust;
  return `${govde}/${alt}`.replace(/\/{2,}/g, '/');
}

/**
 * Verilen TSX kaynağındaki BÜTÜN `<Route>` etiketlerini ayrıştırır.
 *
 * `dosyaYolu` yalnız ihlal iletilerinde adres olarak kullanılır; içerik
 * `kaynak` parametresinden okunur.
 */
export function routeEtiketleriniAyristir(dosyaYolu: string, kaynak: string): AyristirmaSonucu {
  const agac = ts.createSourceFile(dosyaYolu, kaynak, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const hepsi: AyristirilmisRoute[] = [];

  const gez = (dugum: ts.Node, ustYol: string): void => {
    let jsx: ts.JsxOpeningElement | ts.JsxSelfClosingElement | null = null;
    if (ts.isJsxSelfClosingElement(dugum) && etiketAdi(dugum.tagName) === 'Route') jsx = dugum;
    if (ts.isJsxElement(dugum) && etiketAdi(dugum.openingElement.tagName) === 'Route') {
      jsx = dugum.openingElement;
    }

    if (jsx === null) {
      // YASAK ROTA BEYAN BİÇİMLERİ — FAIL-CLOSED.
      //
      // Mimari kural: uygulama rota beyanları YALNIZ src/App.tsx içindeki JSX
      // <Route> hiyerarşisi olabilir. Aşağıdaki iki biçim kapsam kapısının
      // görüş alanının tamamen dışında kalır; sessizce geçilmesi G1'i kör bırakır:
      //
      //   React.createElement(Route, ...)  ← AST gezgini yalnız JSX etiketlerini
      //   useRoutes([...])                   izliyordu; CallExpression atlanıyordu.
      //
      // ÖLÇÜLEN KAÇIŞ (Cursor final runtime review, cc10c27 sonrası):
      // React.createElement(Route, {path: 'kacak', element: <Login/>}) şeklinde
      // eklenen rota G1'i YEŞIL bırakıyordu. Artık FAIL-CLOSED.
      if (ts.isCallExpression(dugum)) {
        const cagri = dugum.expression;
        // React.createElement(Route, ...) veya createElement(Route, ...)
        const createElementAdi =
          ts.isIdentifier(cagri)
            ? cagri.text
            : ts.isPropertyAccessExpression(cagri)
              ? cagri.name.text
              : '';
        if (createElementAdi === 'createElement' && dugum.arguments.length >= 1) {
          const ilkArg = dugum.arguments[0];
          if (ts.isIdentifier(ilkArg) && ilkArg.text === 'Route') {
            hata(
              dosyaYolu,
              dugum,
              agac,
              '`React.createElement(Route, ...)` yasaklıdır — ' +
                'uygulama rota beyanları yalnız src/App.tsx içindeki JSX ' +
                '`<Route>` hiyerarşisi olabilir',
            );
          }
        }
        // useRoutes([...])
        if (ts.isIdentifier(cagri) && cagri.text === 'useRoutes') {
          hata(
            dosyaYolu,
            dugum,
            agac,
            '`useRoutes([...])` yasaklıdır — ' +
              'uygulama rota beyanları yalnız src/App.tsx içindeki JSX ' +
              '`<Route>` hiyerarşisi olabilir',
          );
        }
      }
      dugum.forEachChild(cocuk => gez(cocuk, ustYol));
      return;
    }

    const {line} = agac.getLineAndCharacterOfPosition(jsx.getStart(agac));
    const satir = line + 1;

    let yol: string | null = null;
    let index = false;
    for (const oznitelik of jsx.attributes.properties) {
      if (ts.isJsxSpreadAttribute(oznitelik)) {
        // `<Route {...tanim}/>`: rota kümesi artık kaynaktan OKUNAMAZ.
        hata(dosyaYolu, oznitelik, agac, '`<Route>` yayılmış öznitelik (spread) taşıyor');
      }
      const ad = oznitelik.name.getText();
      if (ad === 'path') yol = ozniteliktenMetin(dosyaYolu, agac, oznitelik);
      else if (ad === 'index') index = indexMi(dosyaYolu, agac, oznitelik);
    }

    if (yol !== null && index) {
      hata(dosyaYolu, jsx, agac, '`<Route>` hem `path` hem `index` taşıyor');
    }

    let kendiYolu = ustYol;
    if (index) {
      hepsi.push({tur: 'index', rota: ustYol, satir});
    } else if (yol === '*') {
      hepsi.push({tur: 'yakalayici', rota: null, satir});
    } else if (yol !== null) {
      kendiYolu = yolBirlestir(ustYol, yol);
      hepsi.push({tur: 'adlandirilmis', rota: kendiYolu, satir});
    } else {
      hepsi.push({tur: 'duzen', rota: null, satir});
    }

    // Çocuklar KENDİ yolunun altında gezilir: iç içe `<Route>` ağaçları da
    // doğru tam yolu alsın.
    if (ts.isJsxElement(dugum)) {
      for (const cocuk of dugum.children) gez(cocuk, kendiYolu);
    }
  };

  agac.forEachChild(dugum => gez(dugum, '/'));

  const rotalar = hepsi
    .filter(girdi => girdi.tur === 'adlandirilmis' || girdi.tur === 'index')
    .map(girdi => girdi.rota as string);

  return {
    hepsi,
    rotalar,
    indexSayisi: hepsi.filter(girdi => girdi.tur === 'index').length,
    yakalayiciSayisi: hepsi.filter(girdi => girdi.tur === 'yakalayici').length,
    duzenSayisi: hepsi.filter(girdi => girdi.tur === 'duzen').length,
  };
}

/** Diskteki bir TSX dosyasını ayrıştırır. */
export function dosyayiAyristir(mutlakYol: string): AyristirmaSonucu {
  return routeEtiketleriniAyristir(mutlakYol, readFileSync(mutlakYol, 'utf8'));
}

/** Kaynakta kaç `<Route` etiketi GEÇTİĞİ — AST sayımıyla karşılaştırmak için. */
export function metindekiRouteEtiketSayisi(kaynak: string): number {
  return [...kaynak.matchAll(/<Route\b/g)].length;
}

/** Kaynakta `useRoutes(` ya da `createElement(Route` geçiyor mu?
 *
 *  G11'in `src/` taramasında kullanılır. Metin tabanlıdır (AST değil):
 *  yorumları ve dize değişmezlerini ayırt etmez, ancak bu kalıpların gerçek
 *  kaynak dosyalarda metin olarak geçmesi zaten kural ihlalidir.
 *
 *  Tasarım notu: AST tabanlı `routeEtiketleriniAyristir` App.tsx'i işlerken
 *  zaten bu kalıpları ROTA AYRIŞTIRILAMADI ile düşürür; bu fonksiyon yalnız
 *  `src/` altındaki DİĞER dosyaları taramak için vardır. */
export function yasakBeyanVarMi(kaynak: string): boolean {
  return /useRoutes\s*\(/.test(kaynak) || /createElement\s*\(\s*Route\b/.test(kaynak);
}
