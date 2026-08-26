import {render,screen} from '@testing-library/react';
import {describe,expect,it} from 'vitest';

import HarmanBrandMark from './HarmanBrandMark';

describe('HarmanBrandMark',()=>{
 it('exposes the agricultural brand mark with an accessible name',()=>{
  render(<HarmanBrandMark/>);

  expect(screen.getByRole('img',{name:'Harman Zamanı logosu'})).toBeInTheDocument();
 });
});
