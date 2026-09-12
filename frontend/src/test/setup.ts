import '@testing-library/jest-dom/vitest';
import {cleanup} from '@testing-library/react';
import {afterEach} from 'vitest';

// vitest `globals` kapalı olduğundan RTL kendi afterEach(cleanup)'ını kaydedemez;
// temizlemeyen bir dosyada önceki testin ağacı bağlı kalır ve sonraki testin
// mock'larına yazmaya devam eder (H37: Transactions ?new=1 → open:true sızıntısı).
afterEach(()=>cleanup());
