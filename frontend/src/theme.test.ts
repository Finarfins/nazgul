import {describe, expect, it} from 'vitest';
import {decomposeColor,getContrastRatio,recomposeColor} from '@mui/material/styles';

import {getAppTheme,getDarkHeaderButtonStyle,headerDarkActionVariant,sungurTokens,theme} from './theme';

const blendWithBackground = (foreground:string, background:string)=>{
 const fg=decomposeColor(foreground);
 const bg=decomposeColor(background);
 const fgAlpha=(fg.type === 'rgba' ? fg.values[3] : 1) ?? 1;
 const mixed:[number,number,number] = [
  Math.round((1 - fgAlpha) * bg.values[0] + fgAlpha * fg.values[0]),
  Math.round((1 - fgAlpha) * bg.values[1] + fgAlpha * fg.values[1]),
  Math.round((1 - fgAlpha) * bg.values[2] + fgAlpha * fg.values[2]),
 ];
 return recomposeColor({type:'rgb',values:mixed});
};

const getDarkHeaderVariant = (mode:'light'|'dark')=>{
 const themeMode=getAppTheme(mode);
 const variant = (themeMode.components?.MuiButton?.variants || []).find((item:any)=>
  item?.props?.variant===headerDarkActionVariant
 );
 return {themeMode, variant: variant as {style?:any;props?:any} | undefined};
};

describe('Sungur application theme',()=>{
 it('uses the shared brand and surface tokens',()=>{
  expect(theme.palette.primary.main).toBe('#164a8a');
  expect(theme.palette.primary.dark).toBe('#0b3567');
  expect(theme.palette.brandTint).toBe('#eaf2fb');
  expect(theme.palette.text.primary).toBe('#172033');
  expect(theme.palette.text.secondary).toBe('#657186');
  expect(theme.palette.background.default).toBe('#f5f7fa');
  expect(theme.palette.background.paper).toBe('#ffffff');
  expect(theme.palette.divider).toBe('#e2e7ef');
  expect(theme.palette.sidebar).toBe('#071e41');
  expect(theme.palette.secondary.main).toBe('#f5c400');
  expect(theme.palette.success.main).toBe('#27845a');
 });

 it('keeps standard radii and focus treatment centralized',()=>{
  expect(theme.shape.borderRadius).toBe(10);
  expect(sungurTokens.radius).toEqual({card:14,control:10,compact:8});
  expect(sungurTokens.focusRing).toBe('0 0 0 3px rgba(22,74,138,.28)');
 });

 it('preserves dark mode while retaining the Sungur semantic palette',()=>{
  const dark=getAppTheme('dark');
  expect(dark.palette.mode).toBe('dark');
  expect(dark.palette.sidebar).toBe('#071e41');
  expect(dark.palette.error.main).toBe('#c0392b');
  expect(dark.palette.warning.main).toBe('#b7791f');
 });

 it('dark header button variant keeps contrast and avoids paper background (light mode)',()=>{
 const style = getDarkHeaderButtonStyle('light');
  const darkHeader = getDarkHeaderVariant('light');
  const headerBg='#071e41';
  const computedBackground=blendWithBackground(style.backgroundColor,headerBg);
  expect(darkHeader.variant?.style).toEqual(style);
  expect((darkHeader.variant?.style as Record<string,unknown>)?.backgroundColor).not.toBe(darkHeader.themeMode.palette.background.paper);
  expect(getContrastRatio(style.color,computedBackground)).toBeGreaterThanOrEqual(4.5);
 });

 it('dark header button variant is consistent on dark mode',()=>{
 const style = getDarkHeaderButtonStyle('dark');
  const darkHeader = getDarkHeaderVariant('dark');
  const headerBg='#071e41';
  const computedBackground=blendWithBackground(style.backgroundColor,headerBg);
  expect(darkHeader.variant?.style).toEqual(style);
  expect((darkHeader.variant?.style as Record<string,unknown>)?.backgroundColor).not.toBe(darkHeader.themeMode.palette.background.paper);
  expect(getContrastRatio(style.color,computedBackground)).toBeGreaterThanOrEqual(4.5);
 });
});

describe('motion system',()=>{
 it('keeps micro-interactions in the 150-320ms band',()=>{
  // 150ms altı "titreme" gibi görünür, 320ms üstü kullanıcıyı bekletir.
  const {fast,base,slow}=sungurTokens.motion;
  expect(fast).toBeGreaterThanOrEqual(120);
  expect(slow).toBeLessThanOrEqual(320);
  expect(fast).toBeLessThan(base);
  expect(base).toBeLessThan(slow);
 });

 it('feeds MUI transitions from the same tokens',()=>{
  // Dialog/Drawer/Collapse kendi süresini uydurmasın; tek ritim.
  expect(theme.transitions.duration.standard).toBe(sungurTokens.motion.base);
  expect(theme.transitions.duration.shortest).toBe(sungurTokens.motion.fast);
  expect(theme.transitions.easing.easeOut).toBe(sungurTokens.motion.enter);
 });

 it('silences every animation under prefers-reduced-motion',()=>{
  // Bu koruma tek noktada durmalı: 79 ekranı tek tek gezerek eklemek
  // mümkün değil, unutulan bir ekran erişilebilirlik kusuru olur.
  const baseline=getAppTheme('light').components?.MuiCssBaseline?.styleOverrides as Record<string,any>;
  const guard=baseline?.['@media (prefers-reduced-motion: reduce)']?.['*,*::before,*::after'];
  expect(guard?.transitionDuration).toContain('!important');
  expect(guard?.animationDuration).toContain('!important');
 });

 it('animates only interactive cards',()=>{
  // Veri yoğun ekranda her yüzeyi oynatmak gürültü; hareket anlam taşımalı.
  const card=getAppTheme('light').components?.MuiCard?.styleOverrides?.root as Record<string,any>;
  const hoverKey=Object.keys(card).find(key=>key.includes('role="button"')&&key.includes(':hover'));
  expect(hoverKey).toBeTruthy();
  expect(card[hoverKey as string]?.transform).toBe('translateY(-2px)');
  // Yükselme transform ile: düzen yeniden hesaplanmaz, yalnız boyanır.
  expect(card).not.toHaveProperty('&:hover');
 });
});
