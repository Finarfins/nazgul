import React from 'react';
import {describe, expect, it} from 'vitest';
import {render, screen} from '@testing-library/react';
import {ThemeProvider} from '@mui/material/styles';
import {Button, Stack} from '@mui/material';
import {headerDarkActionVariant, getAppTheme, getDarkHeaderButtonStyle} from '../theme';

describe('Entity/Products header actions',()=>{
 it('koyu başlık butonları erişilebilir metin ile görünür ve aynı renk varyantını kullanır',()=>{
  const lightTheme=getAppTheme('light');
  const style=getDarkHeaderButtonStyle('light');
  const variant = (lightTheme.components?.MuiButton?.variants || []).find((item:any)=>item?.props?.variant===headerDarkActionVariant);

  render(
   <ThemeProvider theme={lightTheme}>
    <Stack direction="row">
     <Button variant={headerDarkActionVariant}>Ekstre</Button>
     <Button variant={headerDarkActionVariant}>Düzenle</Button>
     <Button variant={headerDarkActionVariant}>Excel&apos;den Aktar</Button>
    </Stack>
   </ThemeProvider>
  );

  expect(screen.getByRole('button',{name:'Ekstre'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Düzenle'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Excel\'den Aktar'})).toBeInTheDocument();
  expect(variant?.style).toEqual(style);
 });
});
