import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import importPlugin from 'eslint-plugin-import';

export default tseslint.config(
  { ignores: ['dist'] },
  {
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
    ],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    settings: { react: { version: 'detect' } },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      react,
      import: importPlugin,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/no-explicit-any': 'warn',
      'react-hooks/exhaustive-deps': 'warn',
      'react-hooks/set-state-in-effect': 'off',
      'import/order': [
        'warn',
        {
          groups: ['builtin', 'external', 'internal', 'parent', 'sibling', 'index'],
          'newlines-between': 'always',
        },
      ],
      'import/no-unresolved': 'off',
    },
  },
  {
    // E2E KLASÖRÜ: REACT KURAL KÜMESİ BURADA KAPALI — GEVŞETME DEĞİL, KATEGORİ
    // HATASININ KALDIRILMASI.
    //
    // `eslint-plugin-react-hooks`, Playwright'ın fixture desenini React hook
    // sanıyor: `test.extend({ page: async ({page}, use) => { await use(x) } })`
    // içindeki `use(...)` çağrısı, adı "use" ile başladığı için hook kuralına
    // takılıyor. Ölçüldü: helpers.ts:48'de `react-hooks/rules-of-hooks` HATASI
    // veriyor. Bu bir kod kusuru değil — bu klasörde React YOK, dolayısıyla
    // kural burada DOĞRU olamaz. Tek örnek gibi görünse de sınıftır: her yeni
    // fixture aynı yanlış pozitifi üretir.
    //
    // KAPSAM BİLEREK DAR: yalnız `e2e/**`. `src` bugünkü her kuralı aynen
    // korur. Kapatılan şey de dar: YALNIZ React eklentilerinin kuralları.
    // `@typescript-eslint`, `import/*` ve `js.configs.recommended` burada
    // AYNEN koşmaya devam eder — CI'daki mutasyon kapısı bunu React'la ilgisi
    // olmayan bir hatayla ayrıca kanıtlar.
    files: ['e2e/**/*.{ts,tsx}'],
    rules: {
      ...Object.fromEntries(
        Object.keys({
          ...reactHooks.configs.recommended.rules,
          ...react.configs.recommended.rules,
          ...react.configs['jsx-runtime'].rules,
        }).map(kural => [kural, 'off']),
      ),
      'react-refresh/only-export-components': 'off',
    },
  }
);
