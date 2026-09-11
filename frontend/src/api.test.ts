import {AxiosError, AxiosHeaders} from 'axios';
import {describe, expect, it} from 'vitest';
import {errorDetail, unwrapApiError} from './api';

const axiosError = (status: number | undefined, data: unknown, message = `Request failed with status code ${status}`): AxiosError => {
  const error = new AxiosError(message);
  if (status !== undefined) {
    error.response = {
      status,
      statusText: '',
      headers: {},
      config: {headers: new AxiosHeaders()},
      data,
    };
  }
  return error;
};

describe('errorDetail', () => {
  it('returns the backend detail string', () => {
    const err = axiosError(409, {detail: 'Firma negatif stok politikası işlemi engelledi'});
    expect(errorDetail(err, 'fallback')).toBe('Firma negatif stok politikası işlemi engelledi');
  });

  it('returns the message of a coded detail object instead of the raw axios text', () => {
    const err = axiosError(409, {detail: {code: 'CEK_GECIS_GECERSIZ', message: 'İzin verilmeyen portföy geçişi: iade -> portfoyde', from: 'iade', to: 'portfoyde'}});
    expect(errorDetail(err, 'fallback')).toBe('İzin verilmeyen portföy geçişi: iade -> portfoyde');
    unwrapApiError(err);
    expect(err.message).toBe('İzin verilmeyen portföy geçişi: iade -> portfoyde');
    expect((err.response?.data as {detail: unknown}).detail).toBe('İzin verilmeyen portföy geçişi: iade -> portfoyde');
  });

  it('falls back when a detail object carries no message', () => {
    const err = axiosError(409, {detail: {code: 'X'}});
    expect(errorDetail(err, 'fallback')).toBe('fallback');
  });

  it('numbers bordro rows and keeps a row-level validator sentence as sent', () => {
    const err = axiosError(422, {detail: [
      {type: 'value_error', loc: ['body', 'satirlar', 2], msg: 'Value error, Alınan evrakta müşteri (customer_id) zorunludur'},
      {type: 'missing', loc: ['body', 'satirlar', 0, 'seri_no'], msg: 'Field required'},
    ]});
    expect(errorDetail(err, 'fallback')).toBe('3. satır: Alınan evrakta müşteri (customer_id) zorunludur 1. satır: seri no zorunlu');
  });

  it('maps 422 validation locations and messages to readable Turkish', () => {
    const err = axiosError(422, {detail: [{type: 'missing', loc: ['body', 'name'], msg: 'Alan zorunlu'}, {msg: 'Geçersiz değer'}]});
    expect(errorDetail(err, 'fallback')).toBe('Ad: zorunlu Geçersiz değer');
  });

  it('identifies the invalid POS line, input and discount limit', () => {
    const err = axiosError(422, {detail: [{
      type: 'less_than_equal',
      loc: ['body', 'items', 1, 'discount_percent'],
      msg: 'Input should be less than or equal to 100',
      input: 'hffh',
      ctx: {le: 100},
    }]});
    expect(errorDetail(err, 'fallback')).toBe('2. satır (hffh): iskonto en fazla %100 olabilir');
  });

  it('falls back when there is no detail', () => {
    expect(errorDetail(axiosError(500, {}), 'fallback')).toBe('fallback');
    expect(errorDetail(new Error('boom'), 'fallback')).toBe('fallback');
  });
});

describe('unwrapApiError', () => {
  it('replaces the raw axios message with the backend detail', () => {
    const err = axiosError(500, {detail: 'Sunucu hatası'});
    unwrapApiError(err);
    expect(err.message).toBe('Sunucu hatası');
  });

  it('maps a detail-less 500 to a clean Turkish message', () => {
    const err = axiosError(500, {});
    unwrapApiError(err);
    expect(err.message).toBe('Sunucu hatası. Lütfen tekrar deneyin.');
    expect(err.message).not.toContain('Request failed');
  });

  it('maps network failures without a response', () => {
    const err = axiosError(undefined, undefined, 'Network Error');
    unwrapApiError(err);
    expect(err.message).toBe('Sunucuya ulaşılamadı. Bağlantınızı kontrol edin.');
  });

  it('leaves non-axios errors untouched', () => {
    const plain = new Error('boom');
    unwrapApiError(plain);
    expect(plain.message).toBe('boom');
  });

  it('normalizes a 422 detail array so it can never be rendered as a React child', () => {
    // Prod incident: pages doing setError(e.response?.data?.detail||'...')
    // pushed the raw Pydantic array into JSX -> React error #31 crash.
    const err = axiosError(422, {detail: [
      {type: 'string_too_short', loc: ['body', 'username'], msg: 'String should have at least 1 character', input: ''},
      {type: 'string_too_short', loc: ['body', 'password'], msg: 'String should have at least 1 character', input: ''},
    ]});
    unwrapApiError(err);
    const detail = (err.response?.data as {detail?: unknown})?.detail;
    expect(typeof detail).toBe('string');
    expect(detail).toBe('Kullanıcı adı: en az 1 karakter olmalı Şifre: en az 1 karakter olmalı');
    expect(err.message).toBe(detail);
  });

  it('does not expose sensitive validation input values', () => {
    const err = axiosError(422, {detail: [{
      type: 'string_too_short',
      loc: ['body', 'password'],
      msg: 'String should have at least 8 characters',
      input: 'gizli',
      ctx: {min_length: 8},
    }]});
    expect(errorDetail(err, 'fallback')).toBe('Şifre: en az 8 karakter olmalı');
    expect(errorDetail(err, 'fallback')).not.toContain('gizli');
  });

  it('keeps a plain string detail exactly as sent', () => {
    const err = axiosError(409, {detail: 'Bu şasi numarası bu firmada zaten kayıtlı'});
    unwrapApiError(err);
    expect((err.response?.data as {detail?: unknown})?.detail).toBe('Bu şasi numarası bu firmada zaten kayıtlı');
  });
});
